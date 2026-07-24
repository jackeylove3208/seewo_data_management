"""Outer retry policy for durable new Agent model batches."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime.observability import agent_observability
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.retry import AgentModelRetriesExhausted
from app.ai.agent_analysis import AgentModelOutputError
from app.ai.agent_analysis_service import AgentAnalysisService, AgentAnalysisWorkItem
from app.ai.providers.base import ModelProviderError
from app.models.agent_analysis import (
    AgentInputRecord,
    AgentModelBatchItemRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.repositories.agent_analysis import AgentAnalysisRepository


async def analyze_with_four_total_attempts[T](operation: Callable[[], Awaitable[T]]) -> T:
    """Run one initial model attempt plus at most three retries."""
    last_error: Exception | None = None
    for _attempt in range(4):
        try:
            return await operation()
        except Exception as error:
            last_error = error
    assert last_error is not None
    raise last_error


class DurableAgentBatchAnalyzer:
    """Persist exactly four bounded model attempts under batch and run lease fencing."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        service: AgentAnalysisService,
    ) -> None:
        self._session_factory = session_factory
        self._service = service

    async def analyze_batch(
        self,
        *,
        batch_id: UUID,
        worker_id: str,
        run_lease_token: UUID,
        lease_seconds: int,
    ) -> AgentModelBatchRecord:
        last_error: Exception | None = None
        for _attempt in range(4):
            async with self._session_factory() as session:
                async with session.begin():
                    repository = AgentAnalysisRepository(session)
                    persisted_attempts = await repository.count_batch_attempts(batch_id)
                    if persisted_attempts >= 4:
                        raise AgentModelRetriesExhausted(4)
                    claim = await repository.claim_batch(
                        batch_id,
                        worker_id=worker_id,
                        run_lease_token=run_lease_token,
                        lease_seconds=lease_seconds,
                    )
                    if claim is None or claim.lease_token is None:
                        raise RuntimeError("Agent model batch is not claimable")
                    attempt = persisted_attempts + 1
                    await AgentRuntimeRepository(session).append_event(
                        claim.run_id,
                        "model_attempt_started",
                        {
                            "phase": "analyze_batches",
                            "batch_id": str(claim.id),
                            "entity_kind": claim.entity_kind,
                            "attempt": attempt,
                            "attempt_count": 4,
                        },
                    )
            try:
                work_items = await self._load_work_items(claim.id)
            except Exception:
                await self._release_batch_claim(
                    batch_id=claim.id,
                    worker_id=worker_id,
                    lease_token=claim.lease_token,
                )
                raise
            agent_observability.observe(
                "model_attempt",
                task_id=claim.task_id,
                run_id=claim.run_id,
                phase="analyze_batches",
                batch_size=len(work_items),
                retry_count=_attempt,
            )
            try:
                findings = await self._service.analyze(
                    tenant_id=claim.tenant_id,
                    task_id=claim.task_id,
                    work_items=work_items,
                )
            except Exception as error:
                if not _is_retryable_model_failure(error):
                    await self._release_batch_claim(
                        batch_id=claim.id,
                        worker_id=worker_id,
                        lease_token=claim.lease_token,
                    )
                    raise
                last_error = error
                try:
                    async with self._session_factory() as session:
                        async with session.begin():
                            repository = AgentAnalysisRepository(session)
                            failed = await repository.append_failed_attempt(
                                batch_id=claim.id,
                                worker_id=worker_id,
                                run_lease_token=run_lease_token,
                                lease_token=claim.lease_token,
                                provider="configured",
                                model="configured",
                                skill_name="reconcile-entity-batch",
                                skill_version="agent-contract-v1",
                                prompt_version="agent-csv-analysis-v1",
                                safe_error_code=_safe_failure_category(error),
                            )
                            await AgentRuntimeRepository(session).append_event(
                                claim.run_id,
                                "model_attempt_failed",
                                {
                                    "phase": "analyze_batches",
                                    "batch_id": str(claim.id),
                                    "entity_kind": claim.entity_kind,
                                    "attempt": failed.attempt_number,
                                    "attempt_count": 4,
                                    "failure_category": failed.safe_error_code,
                                },
                            )
                except Exception:
                    await self._release_batch_claim(
                        batch_id=claim.id,
                        worker_id=worker_id,
                        lease_token=claim.lease_token,
                    )
                    raise
                if failed.attempt_number >= 4:
                    raise AgentModelRetriesExhausted(4) from error
                continue

            agent_observability.observe(
                "analysis_batch",
                task_id=claim.task_id,
                run_id=claim.run_id,
                phase="analyze_batches",
                batch_size=len(work_items),
                retry_count=_attempt,
                outcome="succeeded",
            )
            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        finalized = await AgentAnalysisRepository(session).finalize_batch(
                            batch_id=claim.id,
                            worker_id=worker_id,
                            run_lease_token=run_lease_token,
                            lease_token=claim.lease_token,
                            output_hash="validated-agent-output",
                            findings=findings,
                        )
                        await AgentRuntimeRepository(session).append_event(
                            claim.run_id,
                            "model_attempt_succeeded",
                            {
                                "phase": "analyze_batches",
                                "batch_id": str(claim.id),
                                "entity_kind": claim.entity_kind,
                                "attempt": attempt,
                                "attempt_count": 4,
                            },
                        )
            except Exception:
                await self._release_batch_claim(
                    batch_id=claim.id,
                    worker_id=worker_id,
                    lease_token=claim.lease_token,
                )
                raise
            return finalized
        assert last_error is not None
        raise AgentModelRetriesExhausted(4) from last_error

    async def _load_work_items(self, batch_id: UUID) -> tuple[AgentAnalysisWorkItem, ...]:
        async with self._session_factory() as session:
            rows = tuple(
                await session.execute(
                    select(AgentWorkItemRecord, AgentInputRecord)
                    .join(
                        AgentModelBatchItemRecord,
                        AgentModelBatchItemRecord.work_item_id == AgentWorkItemRecord.id,
                    )
                    .join(
                        AgentInputRecord,
                        AgentInputRecord.id == AgentWorkItemRecord.subject_input_id,
                    )
                    .where(AgentModelBatchItemRecord.batch_id == batch_id)
                    .order_by(AgentModelBatchItemRecord.ordinal)
                )
            )
        return tuple(
            AgentAnalysisWorkItem(
                work_item_id=work.id,
                kind=work.kind,
                entity_kind=input_record.entity_kind,
                locator=input_record.stable_locator,
                fields={
                    "category": input_record.category,
                    "name": input_record.name,
                    "number": input_record.number,
                    "class_name": input_record.class_name,
                    "phone": input_record.phone,
                    "email": input_record.email,
                },
            )
            for work, input_record in rows
        )

    async def _release_batch_claim(
        self,
        *,
        batch_id: UUID,
        worker_id: str,
        lease_token: UUID,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await AgentAnalysisRepository(session).release_batch_claim(
                    batch_id=batch_id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                )


def _safe_failure_category(error: Exception) -> str:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(
            current,
            (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException),
        ):
            return "model_timeout"
        if isinstance(current, httpx.TransportError):
            return "model_transport_failure"
        current = current.__cause__ or current.__context__
    if isinstance(error, ModelProviderError):
        return "model_provider_failure"
    return "model_output_invalid"


def _is_retryable_model_failure(error: Exception) -> bool:
    return isinstance(error, (ModelProviderError, AgentModelOutputError))

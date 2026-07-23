"""Outer retry policy for durable new Agent model batches."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.observability import agent_observability
from app.ai.agent_analysis_service import AgentAnalysisService, AgentAnalysisWorkItem
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
        session: AsyncSession,
        service: AgentAnalysisService,
    ) -> None:
        self._session = session
        self._service = service
        self._repository = AgentAnalysisRepository(session)

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
            claim = await self._repository.claim_batch(
                batch_id,
                worker_id=worker_id,
                run_lease_token=run_lease_token,
                lease_seconds=lease_seconds,
            )
            if claim is None or claim.lease_token is None:
                raise RuntimeError("Agent model batch is not claimable")
            try:
                work_items = await self._load_work_items(claim.id)
                agent_observability.observe(
                    "model_attempt",
                    task_id=claim.task_id,
                    run_id=claim.run_id,
                    phase="analyze_batches",
                    batch_size=len(work_items),
                    retry_count=_attempt,
                )
                findings = await self._service.analyze(
                    tenant_id=claim.tenant_id,
                    task_id=claim.task_id,
                    work_items=work_items,
                )
                agent_observability.observe(
                    "analysis_batch",
                    task_id=claim.task_id,
                    run_id=claim.run_id,
                    phase="analyze_batches",
                    batch_size=len(work_items),
                    retry_count=_attempt,
                    outcome="succeeded",
                )
                return await self._repository.finalize_batch(
                    batch_id=claim.id,
                    worker_id=worker_id,
                    run_lease_token=run_lease_token,
                    lease_token=claim.lease_token,
                    output_hash="validated-agent-output",
                    findings=findings,
                )
            except Exception as error:
                last_error = error
                await self._repository.append_failed_attempt(
                    batch_id=claim.id,
                    worker_id=worker_id,
                    run_lease_token=run_lease_token,
                    lease_token=claim.lease_token,
                    provider="configured",
                    model="configured",
                    skill_name="reconcile-entity-batch",
                    skill_version="agent-contract-v1",
                    prompt_version="agent-csv-analysis-v1",
                    safe_error_code="agent_model_output_or_transport_failure",
                )
        assert last_error is not None
        raise last_error

    async def _load_work_items(self, batch_id: UUID) -> tuple[AgentAnalysisWorkItem, ...]:
        rows = tuple(
            await self._session.execute(
                select(AgentWorkItemRecord, AgentInputRecord)
                .join(
                    AgentModelBatchItemRecord,
                    AgentModelBatchItemRecord.work_item_id == AgentWorkItemRecord.id,
                )
                .join(AgentInputRecord, AgentInputRecord.id == AgentWorkItemRecord.subject_input_id)
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

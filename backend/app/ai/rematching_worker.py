import asyncio
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.providers.base import ModelProviderError, TransientModelError
from app.ai.rematching_policy import manual_review_fallback
from app.ai.rematching_service import EntityRematchingService
from app.repositories.rematching import EntityRematchRepository
from app.schemas.rematching import (
    AcceptCandidateDecision,
    ManualReviewDecision,
    NoMatchDecision,
    RematchDecision,
)


class RematchingAgentProtocol(Protocol):
    async def decide(self, **kwargs: object) -> RematchDecision: ...


class EntityRematchingWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        agent: RematchingAgentProtocol,
        worker_id: str | None = None,
        lease_seconds: int = 60,
        retry_wait_seconds: float = 2,
    ) -> None:
        self.session_factory = session_factory
        self.agent = agent
        self.worker_id = worker_id or f"entity-rematch-worker-{uuid4()}"
        self.lease_seconds = lease_seconds
        self.retry_wait_seconds = retry_wait_seconds

    async def run_once(self) -> bool:
        async with self.session_factory() as claim_session:
            async with claim_session.begin():
                item = await EntityRematchRepository(claim_session).claim_next_available(
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                if item is None:
                    return False
                item_id = item.id
                tenant_id = item.tenant_id
                attempt_count = item.attempt_count
                max_attempts = item.max_attempts

        service = EntityRematchingService(self.agent)  # type: ignore[arg-type]
        async with self.session_factory() as context_session:
            context = await service.prepare(
                context_session,
                item_id=item_id,
                tenant_id=tenant_id,
            )

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_until_stopped(item_id, tenant_id, heartbeat_stop)
        )
        try:
            try:
                decision = await service.decide(context)
            except TransientModelError:
                if attempt_count < max_attempts:
                    async with self.session_factory() as retry_session:
                        async with retry_session.begin():
                            await EntityRematchRepository(retry_session).schedule_retry(
                                item_id,
                                tenant_id,
                                worker_id=self.worker_id,
                                base_delay_seconds=int(self.retry_wait_seconds),
                                failure_code="model_provider_transient",
                            )
                    return True
                decision = manual_review_fallback()
            except ModelProviderError:
                decision = manual_review_fallback()

            async with self.session_factory() as completion_session:
                async with completion_session.begin():
                    await EntityRematchRepository(completion_session).complete_item(
                        item_id,
                        tenant_id,
                        worker_id=self.worker_id,
                        outcome_status=_outcome_status(decision),
                        outcome=decision.model_dump(mode="json"),
                    )
        finally:
            heartbeat_stop.set()
            await heartbeat_task
        return True

    async def _heartbeat_until_stopped(
        self, item_id: UUID, tenant_id: str, stop: asyncio.Event
    ) -> None:
        interval = max(0.1, self.lease_seconds / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                async with self.session_factory() as session:
                    async with session.begin():
                        owned = await EntityRematchRepository(session).heartbeat(
                            item_id,
                            tenant_id,
                            worker_id=self.worker_id,
                            lease_seconds=self.lease_seconds,
                        )
                if not owned:
                    return


def _outcome_status(decision: RematchDecision) -> str:
    if isinstance(decision, AcceptCandidateDecision):
        return "ai_recovered"
    if isinstance(decision, NoMatchDecision):
        return "no_match"
    if isinstance(decision, ManualReviewDecision):
        return "manual_review"
    raise ValueError("unsupported rematching decision")

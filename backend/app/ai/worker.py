import asyncio
import logging
import signal
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.agent import GovernanceAgent
from app.ai.analysis_service import AnalysisExecutionError, AnalysisService
from app.ai.job_service import AnalysisJobService
from app.ai.mcp.server import MCPToolGateway
from app.ai.providers.llm import HttpLLMProvider
from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.security import OperatorContext
from app.models.differences import DifferenceRecord
from app.repositories.analysis_jobs import AnalysisJobRepository
from app.schemas.analysis_jobs import AnalysisWorkItemStatus
from app.schemas.governance import (
    AutoExecutableResolution,
    CauseAnalysisV3,
    ManualResolution,
    NeedsInformationResolution,
    ResolutionMode,
)

AnalyzerFactory = Callable[[AsyncSession, OperatorContext], AnalysisService]
logger = logging.getLogger(__name__)


class WorkerRunner(Protocol):
    async def run_once(self) -> bool: ...


class AnalysisWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        analyzer_factory: AnalyzerFactory,
        worker_id: str | None = None,
        lease_seconds: int = 60,
        retry_wait_seconds: float = 2,
    ) -> None:
        self.session_factory = session_factory
        self.analyzer_factory = analyzer_factory
        self.worker_id = worker_id or f"analysis-worker-{uuid4()}"
        self.lease_seconds = lease_seconds
        self.retry_wait_seconds = retry_wait_seconds

    async def run_once(self) -> bool:
        async with self.session_factory() as claim_session:
            async with claim_session.begin():
                item = await AnalysisJobRepository(claim_session).claim_next_available(
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                if item is None:
                    return False
                item_id = item.id
                job_id = item.job_id
                difference_id = item.difference_id
                attempt_count = item.attempt_count
                max_attempts = item.max_attempts
                tenant_id = item.tenant_id
                difference_version = item.difference_version

        operator = OperatorContext(operator_id=self.worker_id, tenant_id=tenant_id)
        if not await self._difference_is_current(
            difference_id,
            difference_version=difference_version,
            tenant_id=tenant_id,
        ):
            await self._complete_superseded(item_id, job_id, operator)
            return True
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_until_stopped(item_id, heartbeat_stop))
        try:
            try:
                async with self.session_factory() as analysis_session:
                    analyzer = self.analyzer_factory(analysis_session, operator)
                    result = await analyzer.analyze_v3(
                        difference_id,
                        fallback_on_failure=False,
                    )
                    await analysis_session.commit()
            except AnalysisExecutionError as error:
                if error.transient and attempt_count < max_attempts:
                    available_at = datetime.now(UTC) + timedelta(
                        seconds=self.retry_wait_seconds * (2 ** (attempt_count - 1))
                    )
                    async with self.session_factory() as retry_session:
                        async with retry_session.begin():
                            await AnalysisJobRepository(retry_session).schedule_retry(
                                item_id,
                                worker_id=self.worker_id,
                                available_at=available_at,
                                failure_code=error.failure_code,
                            )
                    return True
                async with self.session_factory() as fallback_session:
                    analyzer = self.analyzer_factory(fallback_session, operator)
                    result = await analyzer.persist_v3_fallback(
                        difference_id,
                        failure_code=error.failure_code,
                        attempt_count=attempt_count,
                        provenance=error.provenance,
                    )
                    await fallback_session.commit()

            outcome, resolution_mode = _resolution_outcome(result.output)
            async with self.session_factory() as completion_session:
                async with completion_session.begin():
                    current_version = await completion_session.scalar(
                        select(DifferenceRecord.version)
                        .where(
                            DifferenceRecord.id == difference_id,
                            DifferenceRecord.tenant_id == tenant_id,
                        )
                        .with_for_update()
                    )
                    repository = AnalysisJobRepository(completion_session)
                    if current_version != difference_version:
                        await repository.complete_item(
                            item_id,
                            worker_id=self.worker_id,
                            outcome=AnalysisWorkItemStatus.SUPERSEDED,
                            result_id=result.id,
                            failure_code="difference_version_superseded",
                        )
                    else:
                        await repository.complete_item(
                            item_id,
                            worker_id=self.worker_id,
                            outcome=outcome,
                            result_id=result.id,
                            failure_code=result.failure_code,
                            resolution_mode=resolution_mode,
                        )
        finally:
            heartbeat_stop.set()
            await heartbeat_task
        await self._sync_workflow(job_id, operator)
        return True

    async def _difference_is_current(
        self,
        difference_id: UUID,
        *,
        difference_version: int,
        tenant_id: str,
    ) -> bool:
        async with self.session_factory() as session:
            current_version = await session.scalar(
                select(DifferenceRecord.version).where(
                    DifferenceRecord.id == difference_id,
                    DifferenceRecord.tenant_id == tenant_id,
                )
            )
        return current_version == difference_version

    async def _complete_superseded(
        self,
        item_id: UUID,
        job_id: UUID,
        operator: OperatorContext,
    ) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                repository = AnalysisJobRepository(session)
                await repository.complete_item(
                    item_id,
                    worker_id=self.worker_id,
                    outcome=AnalysisWorkItemStatus.SUPERSEDED,
                    failure_code="difference_version_superseded",
                )
        await self._sync_workflow(job_id, operator)

    async def _sync_workflow(self, job_id: UUID, operator: OperatorContext) -> None:
        async with self.session_factory() as workflow_session:
            async with workflow_session.begin():
                job = await AnalysisJobRepository(workflow_session).get(job_id)
                if job is not None:
                    await AnalysisJobService(
                        workflow_session,
                        operator=operator,
                    ).sync_workflow(job)

    async def _heartbeat_until_stopped(
        self,
        item_id: UUID,
        stop: asyncio.Event,
    ) -> None:
        interval = max(0.1, self.lease_seconds / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                async with self.session_factory() as heartbeat_session:
                    async with heartbeat_session.begin():
                        owned = await AnalysisJobRepository(heartbeat_session).heartbeat(
                            item_id,
                            worker_id=self.worker_id,
                            lease_seconds=self.lease_seconds,
                        )
                if not owned:
                    return


async def run_worker(settings: Settings | None = None) -> None:
    configured = settings or get_settings()
    database = Database(configured.database_url)
    tokenization_secret = (
        configured.tokenization_secret.get_secret_value()
        if configured.tokenization_secret is not None
        else None
    )

    def analyzer_factory(session: AsyncSession, operator: OperatorContext) -> AnalysisService:
        return AnalysisService(
            session,
            agent=GovernanceAgent(
                HttpLLMProvider(settings=configured),
                MCPToolGateway(session),
                tokenization_secret=tokenization_secret,
            ),
            operator=operator,
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop.set)
    try:
        workers = tuple(
            AnalysisWorker(
                database.session_factory,
                analyzer_factory=analyzer_factory,
                worker_id=f"analysis-worker-{uuid4()}-{index}",
                lease_seconds=configured.analysis_worker_lease_seconds,
                retry_wait_seconds=configured.analysis_worker_retry_wait_seconds,
            )
            for index in range(effective_worker_concurrency(configured, database))
        )

        await asyncio.gather(
            *(
                run_worker_loop(
                    worker,
                    stop,
                    poll_seconds=configured.analysis_worker_poll_seconds,
                )
                for worker in workers
            )
        )
    finally:
        await database.dispose()


def effective_worker_concurrency(settings: Settings, database: Database) -> int:
    if database.engine.dialect.name == "sqlite":
        return 1
    return settings.analysis_worker_concurrency


async def run_worker_loop(
    worker: WorkerRunner,
    stop: asyncio.Event,
    *,
    poll_seconds: float,
) -> None:
    while not stop.is_set():
        try:
            worked = await worker.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("analysis worker item failed; continuing")
            worked = False
        if worked:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass


def _resolution_outcome(
    output: CauseAnalysisV3 | object | None,
) -> tuple[AnalysisWorkItemStatus, ResolutionMode]:
    if not isinstance(output, CauseAnalysisV3):
        raise ValueError("analysis-v3 worker requires a v3 result")
    recommended = next(
        solution
        for solution in output.solutions
        if solution.solution_id == output.recommended_solution_id
    )
    if isinstance(recommended, AutoExecutableResolution):
        return AnalysisWorkItemStatus.SUCCEEDED, ResolutionMode.AUTO_EXECUTABLE
    if isinstance(recommended, NeedsInformationResolution):
        return AnalysisWorkItemStatus.MANUAL_REQUIRED, ResolutionMode.NEEDS_INFORMATION
    if isinstance(recommended, ManualResolution):
        return AnalysisWorkItemStatus.MANUAL_REQUIRED, ResolutionMode.MANUAL_ONLY
    raise ValueError("analysis-v3 result has an unsupported recommended resolution")


if __name__ == "__main__":
    asyncio.run(run_worker())

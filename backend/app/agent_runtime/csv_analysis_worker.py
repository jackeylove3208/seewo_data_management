"""Worker handlers for the analysis-only CSV Agent milestone."""

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime.csv_analysis_handlers import AgentIngestionPhaseHandler
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase, AgentRunStatus
from app.agent_runtime.worker import AgentWorkContext, AgentWorkResult
from app.ai.agent_analysis_service import AgentAnalysisService, SingleAttemptModelProvider
from app.ai.agent_batching import AgentBatchPlanner
from app.ai.agent_durable_analysis import DurableAgentBatchAnalyzer
from app.ai.providers.llm import HttpLLMProvider
from app.core.config import get_settings
from app.models.agent_analysis import AgentModelBatchRecord, AgentWorkItemRecord
from app.reconciliation.agent_identity import AgentIdentityIndexBuilder

AgentHandler = Callable[[AgentWorkContext], Awaitable[AgentWorkResult]]


class CsvAnalysisHandlerFactory:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        tokenization_secret: str,
        provider: SingleAttemptModelProvider | None = None,
        lease_seconds: int = 60,
    ) -> None:
        self._session_factory = session_factory
        self._tokenization_secret = tokenization_secret
        self._provider = provider or HttpLLMProvider(settings=get_settings())
        self._lease_seconds = lease_seconds

    def handlers(self) -> dict[AgentPhase, AgentHandler]:
        return {
            AgentPhase.INGEST_AND_NORMALIZE: self.ingest,
            AgentPhase.BUILD_IDENTITY_WORK: self.build_identity_work,
            AgentPhase.ANALYZE_BATCHES: self.analyze_batches,
            AgentPhase.CLARIFY_IDENTITY_CONFLICTS: self.wait_for_clarification,
        }

    async def ingest(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                await AgentIngestionPhaseHandler(session).ingest(run_id=context.run_id)
        return AgentWorkResult(next_phase=AgentPhase.BUILD_IDENTITY_WORK)

    async def build_identity_work(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                await AgentIdentityIndexBuilder(session).build(run_id=context.run_id)
                await AgentBatchPlanner(session).create_for_run(run_id=context.run_id)
                await AgentRuntimeRepository(session).append_event(
                    context.run_id, "agent_identity_work_persisted", {}
                )
        return AgentWorkResult(next_phase=AgentPhase.ANALYZE_BATCHES)

    async def analyze_batches(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                service = AgentAnalysisService(
                    self._provider, tokenization_secret=self._tokenization_secret
                )
                analyzer = DurableAgentBatchAnalyzer(session, service)
                batches = tuple(
                    await session.scalars(
                        select(AgentModelBatchRecord)
                        .where(
                            AgentModelBatchRecord.run_id == context.run_id,
                            AgentModelBatchRecord.status == "pending",
                        )
                        .order_by(AgentModelBatchRecord.created_at, AgentModelBatchRecord.id)
                    )
                )
                try:
                    for batch in batches:
                        await analyzer.analyze_batch(
                            batch_id=batch.id,
                            worker_id=context.worker_id,
                            run_lease_token=context.lease_token,
                            lease_seconds=self._lease_seconds,
                        )
                except Exception:
                    runtime = AgentRuntimeRepository(session)
                    await runtime.record_failure(
                        context.run_id,
                        phase=AgentPhase.ANALYZE_BATCHES,
                        code="agent_model_retries_exhausted",
                        safe_message="AI 模型连续处理失败，任务已安全暂停；当前仅允许终止任务。",
                        attempt_count=4,
                    )
                    await runtime.append_event(
                        context.run_id,
                        "run.blocked_model_error",
                        {"allowed_commands": ["terminate"]},
                    )
                    return AgentWorkResult(next_status=AgentRunStatus.BLOCKED_MODEL_ERROR)
                has_conflict = await session.scalar(
                    select(AgentWorkItemRecord.id).where(
                        AgentWorkItemRecord.run_id == context.run_id,
                        AgentWorkItemRecord.kind == "identity_conflict",
                    )
                )
                await AgentRuntimeRepository(session).append_event(
                    context.run_id, "agent_analysis_completed", {"batch_count": len(batches)}
                )
        if has_conflict is not None:
            return AgentWorkResult(next_phase=AgentPhase.CLARIFY_IDENTITY_CONFLICTS)
        return AgentWorkResult(next_status=AgentRunStatus.WAITING_HUMAN)

    async def wait_for_clarification(self, _context: AgentWorkContext) -> AgentWorkResult:
        return AgentWorkResult(next_status=AgentRunStatus.WAITING_HUMAN)

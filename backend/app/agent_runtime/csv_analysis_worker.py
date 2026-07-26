"""Worker handlers for the analysis-only CSV Agent milestone."""

from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.csv_analysis_handlers import AgentIngestionPhaseHandler
from app.agent_runtime.csv_governance_handlers import CsvGovernanceHandlers
from app.agent_runtime.csv_rollback_handlers import CsvRollbackHandlers
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.retry import AgentModelRetriesExhausted
from app.agent_runtime.state_machine import AgentPhase, AgentRunStatus
from app.agent_runtime.worker import AgentWorkContext, AgentWorkResult
from app.ai.agent_analysis_service import AgentAnalysisService, SingleAttemptModelProvider
from app.ai.agent_batching import AgentBatchPlanner
from app.ai.agent_durable_analysis import DurableAgentBatchAnalyzer
from app.ai.providers.llm import HttpLLMProvider
from app.core.config import Settings, get_settings
from app.ingestion.agent_contract import AgentContractError
from app.models.agent_analysis import AgentModelBatchRecord
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask
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
        analysis_only: bool | None = None,
        csv_execution_enabled: bool | None = None,
        output_root: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._tokenization_secret = tokenization_secret
        self._provider = provider or HttpLLMProvider(settings=get_settings())
        self._lease_seconds = lease_seconds
        settings = settings or get_settings()
        self._analysis_only = (
            settings.new_agent_analysis_only if analysis_only is None else analysis_only
        )
        self._csv_execution_enabled = (
            settings.new_agent_csv_execution_enabled
            if csv_execution_enabled is None
            else csv_execution_enabled
        )
        self._governance = CsvGovernanceHandlers(
            output_root=output_root or settings.export_root / "agent-targets",
            settings=settings,
        )
        self._rollback = CsvRollbackHandlers(
            output_root=output_root or settings.export_root / "agent-targets",
            settings=settings,
        )

    def handlers(self) -> dict[AgentPhase, AgentHandler]:
        return {
            AgentPhase.INGEST_AND_NORMALIZE: self.ingest,
            AgentPhase.BUILD_IDENTITY_WORK: self.build_identity_work,
            AgentPhase.ANALYZE_BATCHES: self.analyze_batches,
            AgentPhase.CLARIFY_IDENTITY_CONFLICTS: self.wait_for_clarification,
            AgentPhase.AGGREGATE_RISK_AND_APPROVALS: self.aggregate_risk,
            AgentPhase.COMPILE_EXECUTION_PLAN: self.compile_plan,
            AgentPhase.EXECUTE_AND_VERIFY: self.execute_and_verify,
            AgentPhase.GENERATE_REPORT: self.generate_report,
            AgentPhase.PLAN_RESTORE: self.plan_restore,
            AgentPhase.CLARIFY_RESTORE_CONFLICTS: self.clarify_restore,
            AgentPhase.APPROVE_RESTORE: self.approve_restore,
            AgentPhase.EXECUTE_RESTORE: self.execute_restore,
            AgentPhase.REPORT_RESTORE: self.report_restore,
        }

    async def ingest(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                try:
                    await AgentIngestionPhaseHandler(session).ingest(run_id=context.run_id)
                except AgentContractError as error:
                    run = await session.get(AgentRunRecord, context.run_id)
                    task = await session.get(ReconciliationTask, context.task_id)
                    if run is None or task is None:
                        raise LookupError(
                            "Agent abnormal-input context is missing"
                        ) from error
                    await AgentReportingService(session).generate(
                        task_id=task.id,
                        tenant_id=task.tenant_id,
                        kind=run.kind,
                        terminal_state="abnormal_input",
                        facts={
                            "mutations": [],
                            "input_error": {
                                "code": "unrecognizable_input_schema",
                                "message": "输入字段无法识别，任务未进入分析或治理。",
                            },
                        },
                    )
                    task.status = "abnormal_input"
                    task.stage = "reporting"
                    await AgentRuntimeRepository(session).append_event(
                        run.id,
                        "abnormal_input_report_ready",
                        {"code": "unrecognizable_input_schema"},
                    )
                    return AgentWorkResult(next_phase=AgentPhase.GENERATE_REPORT)
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
            batches = tuple(
                await session.scalars(
                    select(AgentModelBatchRecord)
                    .where(
                        AgentModelBatchRecord.run_id == context.run_id,
                        AgentModelBatchRecord.status != "completed",
                    )
                    .order_by(AgentModelBatchRecord.created_at, AgentModelBatchRecord.id)
                )
            )
        service = AgentAnalysisService(
            self._provider, tokenization_secret=self._tokenization_secret
        )
        analyzer = DurableAgentBatchAnalyzer(self._session_factory, service)
        try:
            for batch in batches:
                await analyzer.analyze_batch(
                    batch_id=batch.id,
                    worker_id=context.worker_id,
                    run_lease_token=context.lease_token,
                    lease_seconds=self._lease_seconds,
                )
        except AgentModelRetriesExhausted as error:
            async with self._session_factory() as session:
                async with session.begin():
                    runtime = AgentRuntimeRepository(session)
                    await runtime.record_failure(
                        context.run_id,
                        phase=AgentPhase.ANALYZE_BATCHES,
                        code="agent_model_retries_exhausted",
                        safe_message="AI 模型连续处理失败，任务已安全暂停；当前仅允许终止任务。",
                        attempt_count=error.attempt_count,
                    )
                    await runtime.append_event(
                        context.run_id,
                        "model_retry_exhausted",
                        {
                            "code": "agent_model_retries_exhausted",
                            "message": "AI 模型连续处理失败，任务已安全暂停。",
                            "attempt_count": error.attempt_count,
                            "allowed_commands": ["terminate"],
                        },
                    )
            return AgentWorkResult(next_status=AgentRunStatus.BLOCKED_MODEL_ERROR)
        async with self._session_factory() as session:
            async with session.begin():
                await AgentRuntimeRepository(session).append_event(
                    context.run_id, "agent_analysis_completed", {"batch_count": len(batches)}
                )
        if self._analysis_only:
            return AgentWorkResult(next_status=AgentRunStatus.WAITING_HUMAN)
        return AgentWorkResult(next_phase=AgentPhase.CLARIFY_IDENTITY_CONFLICTS)

    async def wait_for_clarification(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            from app.models.agent_analysis import AgentClarificationRecord

            unresolved = await session.scalar(
                select(AgentClarificationRecord.id).where(
                    AgentClarificationRecord.run_id == context.run_id,
                    AgentClarificationRecord.status.in_(("pending", "interpreted")),
                )
            )
        if unresolved is not None:
            return AgentWorkResult(next_status=AgentRunStatus.WAITING_HUMAN)
        return AgentWorkResult(next_phase=AgentPhase.AGGREGATE_RISK_AND_APPROVALS)

    async def aggregate_risk(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                return await self._governance.aggregate(session, context)

    async def compile_plan(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                return await self._governance.compile(session, context)

    async def execute_and_verify(self, context: AgentWorkContext) -> AgentWorkResult:
        if not self._csv_execution_enabled:
            return AgentWorkResult(next_phase=AgentPhase.GENERATE_REPORT)
        async with self._session_factory() as session:
            async with session.begin():
                return await self._governance.execute(session, context)

    async def generate_report(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                return await self._governance.report(session, context)

    async def plan_restore(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                return await self._rollback.plan(session, context)

    async def clarify_restore(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                return await self._rollback.clarify(session, context)

    async def approve_restore(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                return await self._rollback.approve(session, context)

    async def execute_restore(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                return await self._rollback.execute(session, context)

    async def report_restore(self, context: AgentWorkContext) -> AgentWorkResult:
        async with self._session_factory() as session:
            async with session.begin():
                return await self._rollback.report(session, context)

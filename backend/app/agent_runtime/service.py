import re
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_graph.repository import AgentGraphRepository
from app.agent_reporting.rollback_cycles import (
    AgentRollbackCycleService,
    require_rollback_cycle_generation,
)
from app.agent_runtime.repository import AgentRunNotFound, AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase, AgentRunKind, AgentRunStatus
from app.core.config import Settings
from app.core.security import OperatorContext
from app.models.agent_analysis import AgentGovernanceOperationRecord
from app.models.agent_runtime import AgentRunRecord, SchoolTaskLockRecord
from app.models.reconciliation import ReconciliationTask

MODEL_FAILURES = {
    "retries_exhausted": (
        "agent_model_retries_exhausted",
        "AI 模型连续处理失败，任务已安全暂停；当前仅允许终止任务。",
    ),
}
SAFE_GATEWAY_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class AgentSupervisorService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        operator: OperatorContext,
        repository: AgentRuntimeRepository | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.operator = operator
        self.repository = repository or AgentRuntimeRepository(session)
        self.settings = settings

    async def start(
        self,
        *,
        task_id: UUID,
        conversation_id: UUID | None,
    ) -> AgentRunRecord:
        tenant_id = self.operator.tenant_id
        task = await self.session.scalar(
            select(ReconciliationTask)
            .where(
                ReconciliationTask.id == task_id,
                ReconciliationTask.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if task is None:
            raise LookupError(f"reconciliation task not found: {task_id}")
        if task.workflow_version not in {"new-agent-v1", "agent-graph-v1"}:
            raise ValueError(
                f"Agent supervisor cannot process task version {task.workflow_version}"
            )
        if conversation_id is not None:
            conversation = await self.repository.get_active_conversation(
                conversation_id, tenant_id=tenant_id
            )
            if conversation is None:
                raise LookupError(f"active conversation not found: {conversation_id}")
        existing = await self.repository.get_run_for_task(task_id, for_update=True)
        if existing is not None:
            return existing

        run = await self.repository.create_run(
            task_id=task.id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            kind=AgentRunKind.SYNC,
            workflow_version=task.workflow_version,
            ingestion_contract_version=(
                "source-ingestion-v3"
                if self.settings is not None
                and self.settings.source_ingestion_v3_enabled
                and task.workflow_version == "agent-graph-v1"
                and _uses_api_authority(task)
                else (
                    "source-ingestion-v2"
                    if self.settings is not None
                    and self.settings.source_ingestion_v2_enabled
                    and task.workflow_version == "agent-graph-v1"
                    else "model-mediated-ingestion-v1"
                )
            ),
            execution_contract_version=(
                "deterministic-execution-v2"
                if self.settings is not None
                and (
                    self.settings.source_ingestion_v2_enabled
                    or (
                        self.settings.source_ingestion_v3_enabled
                        and _uses_api_authority(task)
                    )
                )
                and task.workflow_version == "agent-graph-v1"
                else "model-mediated-execution-v1"
            ),
        )
        await self.repository.append_event(
            run.id,
            "run.created",
            {
                "phase": run.phase,
                "status": run.status,
                "workflow_version": run.workflow_version,
                "ingestion_contract_version": run.ingestion_contract_version,
                "execution_contract_version": run.execution_contract_version,
            },
        )
        graph_repository = (
            AgentGraphRepository(self.session)
            if task.workflow_version == "agent-graph-v1"
            else None
        )
        graph_version = (
            "agent-sync-graph-v2"
            if _uses_remote_csv(task) or _uses_api_authority(task)
            else "agent-sync-graph-v1"
        )
        graph_state = (
            await graph_repository.create_run_state(
                run_id=run.id,
                graph_version=graph_version,
                initial_node="intent_confirmed",
            )
            if graph_repository is not None
            else None
        )
        run = await self.repository.transition_run(
            run.id, requested_phase=AgentPhase.ACQUIRE_SCHOOL_LOCK
        )
        if graph_repository is not None and graph_state is not None:
            await graph_repository.record_transition(
                graph_state.id,
                expected_cursor=0,
                from_node="intent_confirmed",
                to_node="acquire_school_lock",
                action_id="acquire_school_lock",
                guard_results={"workflow_version": "passed", "tenant": "passed"},
                fencing_token=run.attempt_count,
            )
        await self.repository.acquire_school_lock(
            tenant_id=tenant_id,
            task_id=task.id,
            run_id=run.id,
        )
        await self.repository.append_event(
            run.id,
            "school_lock.acquired",
            {"phase": run.phase, "status": run.status},
        )
        run = await self.repository.transition_run(
            run.id, requested_phase=AgentPhase.INGEST_AND_NORMALIZE
        )
        if graph_repository is not None and graph_state is not None:
            after_lock_node = (
                "materialize_sources"
                if graph_version == "agent-sync-graph-v2"
                else "inspect_sources"
            )
            await graph_repository.record_transition(
                graph_state.id,
                expected_cursor=1,
                from_node="acquire_school_lock",
                to_node=after_lock_node,
                action_id=after_lock_node,
                guard_results={"school_lock": "passed"},
                fencing_token=run.attempt_count,
            )
        await self.repository.append_event(
            run.id,
            "phase.started",
            {"phase": run.phase, "status": run.status},
        )
        return run

    async def block_model_failure(
        self,
        *,
        run_id: UUID,
        reason: str,
        attempt_count: int,
        gateway_request_id: str | None = None,
        unsafe_provider_detail: str | None = None,
    ) -> AgentRunRecord:
        del unsafe_provider_detail
        code, safe_message = MODEL_FAILURES.get(
            reason,
            (
                "agent_model_failure",
                "AI 模型处理失败，任务已安全暂停；当前仅允许终止任务。",
            ),
        )
        gateway_request_id = (
            gateway_request_id
            if gateway_request_id is not None
            and SAFE_GATEWAY_REQUEST_ID.fullmatch(gateway_request_id)
            else None
        )
        run = await self.repository.get_run(run_id, for_update=True)
        if run is None:
            raise AgentRunNotFound(str(run_id))
        if run.tenant_id != self.operator.tenant_id:
            raise AgentRunNotFound(str(run_id))
        await self.repository.record_failure(
            run_id,
            phase=AgentPhase(run.phase),
            code=code,
            safe_message=safe_message,
            attempt_count=attempt_count,
            gateway_request_id=gateway_request_id,
        )
        blocked = await self.repository.transition_run(
            run_id, requested_status=AgentRunStatus.BLOCKED_MODEL_ERROR
        )
        await self.repository.append_event(
            run_id,
            "run.blocked_model_error",
            {
                "code": code,
                "message": safe_message,
                "phase": blocked.phase,
                "attempt_count": attempt_count,
                "gateway_request_id": gateway_request_id,
                "allowed_commands": ["terminate"],
            },
        )
        return blocked

    async def confirm_rollback(self, *, task_id: UUID) -> AgentRunRecord:
        task = await self.session.scalar(
            select(ReconciliationTask)
            .where(
                ReconciliationTask.id == task_id,
                ReconciliationTask.tenant_id == self.operator.tenant_id,
                ReconciliationTask.workflow_version.in_(("new-agent-v1", "agent-graph-v1")),
                ReconciliationTask.task_kind == AgentRunKind.ROLLBACK.value,
            )
            .with_for_update()
        )
        if task is None:
            raise LookupError("rollback Agent task not found")
        run = await self.repository.get_run_for_task(task.id, for_update=True)
        if run is None or run.kind != AgentRunKind.ROLLBACK.value:
            raise LookupError("rollback Agent runtime not found")
        if (
            run.phase != AgentPhase.INTENT_CONFIRMED.value
            or run.status != AgentRunStatus.PENDING.value
        ):
            raise ValueError("rollback Agent task is already confirmed")
        await AgentRollbackCycleService(self.session).ensure_available(
            task,
            expected_generation=require_rollback_cycle_generation(task),
        )
        run = await self.repository.transition_run(
            run.id, requested_phase=AgentPhase.ACQUIRE_SCHOOL_LOCK
        )
        graph_repository = (
            AgentGraphRepository(self.session)
            if task.workflow_version == "agent-graph-v1"
            else None
        )
        graph_state = (
            await graph_repository.get_run_state_for_agent_run(run.id)
            if graph_repository is not None
            else None
        )
        if graph_repository is not None and graph_state is not None:
            await graph_repository.record_transition(
                graph_state.id,
                expected_cursor=0,
                from_node="rollback_intent_confirmed",
                to_node="acquire_school_lock",
                action_id="acquire_school_lock",
                guard_results={"workflow_version": "passed", "tenant": "passed"},
                fencing_token=run.attempt_count,
            )
        await self.repository.acquire_school_lock(
            tenant_id=task.tenant_id,
            task_id=task.id,
            run_id=run.id,
        )
        await self.repository.append_event(
            run.id,
            "rollback.confirmed",
            {"phase": run.phase, "status": run.status},
        )
        run = await self.repository.transition_run(run.id, requested_phase=AgentPhase.PLAN_RESTORE)
        if graph_repository is not None and graph_state is not None:
            await graph_repository.record_transition(
                graph_state.id,
                expected_cursor=1,
                from_node="acquire_school_lock",
                to_node="load_verified_mutations",
                action_id="load_verified_mutations",
                guard_results={"school_lock": "passed", "report_facts": "passed"},
                fencing_token=run.attempt_count,
            )
        await self.repository.append_event(
            run.id,
            "phase.started",
            {"phase": run.phase, "status": run.status},
        )
        return run

    async def terminate(self, *, run_id: UUID, reason: str) -> AgentRunRecord:
        """Persist a deterministic terminal summary before releasing the school lock."""
        run = await self.repository.get_run(run_id, for_update=True)
        if run is None or run.tenant_id != self.operator.tenant_id:
            raise LookupError("Agent run not found")
        current_status = AgentRunStatus(run.status)
        if current_status in {AgentRunStatus.COMPLETED, AgentRunStatus.TERMINATED}:
            return run
        if run.workflow_version == "agent-graph-v1":
            graph = await AgentGraphRepository(self.session).get_run_state_for_agent_run(
                run.id, for_update=True
            )
            if graph is None:
                raise LookupError("Agent graph state is missing")
            graph.termination_requested = True
            if current_status in {
                AgentRunStatus.WAITING_HUMAN,
                AgentRunStatus.BLOCKED_MODEL_ERROR,
            }:
                run.status = AgentRunStatus.RUNNING.value
            await self.repository.append_event(
                run.id,
                "graph.termination_requested",
                {
                    "reason": reason[:128],
                    "current_node": graph.current_node,
                    "drain_current_atomic_unit": True,
                },
            )
            return run
        if current_status is not AgentRunStatus.TERMINATING:
            run = await self.repository.transition_run(
                run_id, requested_status=AgentRunStatus.TERMINATING
            )
        await self.repository.append_event(
            run_id,
            "termination.report.persisted",
            {
                "reason": reason[:128],
                "phase": run.phase,
                "status": "terminated",
                "facts_only": True,
                "mutations_preserved": True,
            },
        )
        from app.agent_reporting.service import AgentReportingService

        operations = tuple(
            await self.session.scalars(
                select(AgentGovernanceOperationRecord).where(
                    AgentGovernanceOperationRecord.run_id == run.id
                )
            )
        )
        mutations = _termination_mutations(operations)
        if run.kind == AgentRunKind.ROLLBACK.value:
            checkpoint = await self.repository.get_checkpoint(
                run.id,
                phase=AgentPhase.EXECUTE_RESTORE,
                checkpoint_key="agent-csv-rollback-execution-v1",
            )
            if checkpoint is not None:
                mutations = list(checkpoint.payload.get("mutations", mutations))
        await AgentReportingService(self.session).generate(
            task_id=run.task_id,
            tenant_id=run.tenant_id,
            kind=run.kind,
            terminal_state="terminated",
            facts={"mutations": mutations, "termination_reason": reason[:128]},
        )
        task = await self.session.get(ReconciliationTask, run.task_id)
        if task is not None:
            task.status = "terminated"
            task.stage = "terminal"
        run = await self.repository.transition_run(
            run_id, requested_status=AgentRunStatus.TERMINATED
        )
        await self.repository.append_event(
            run_id,
            "run.terminated",
            {"phase": run.phase, "status": run.status, "reason": reason[:128]},
        )
        active_lock = await self.session.scalar(
            select(SchoolTaskLockRecord.id).where(
                SchoolTaskLockRecord.tenant_id == self.operator.tenant_id,
                SchoolTaskLockRecord.owner_run_id == run_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )
        if active_lock is not None:
            await self.repository.release_school_lock(
                tenant_id=self.operator.tenant_id,
                run_id=run_id,
                reason="terminated",
            )
        return run


def _termination_mutations(
    operations: Iterable[AgentGovernanceOperationRecord | Any],
) -> list[dict[str, object]]:
    """Convert persisted outcomes to report facts without inventing execution results."""
    return [
        {
            "id": str(operation.id),
            "status": str(operation.status),
            "operation": str(operation.operation_type),
            "entity_kind": str(operation.entity_kind),
            "target_source_identifier": operation.target_source_identifier,
            "before": operation.before,
            "after": operation.actual_after,
            "verification": operation.verification or {"valid": operation.status == "succeeded"},
        }
        for operation in operations
    ]


def _uses_remote_csv(task: ReconciliationTask) -> bool:
    if not isinstance(task.agent_intent, dict):
        return False
    source = task.agent_intent.get("source")
    return isinstance(source, dict) and source.get("kind") == "remote_csv"


def _uses_api_authority(task: ReconciliationTask) -> bool:
    if not isinstance(task.agent_intent, dict):
        return False
    source = task.agent_intent.get("source")
    return isinstance(source, dict) and source.get("kind") == "api"

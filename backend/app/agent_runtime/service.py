import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.repository import AgentRunNotFound, AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase, AgentRunKind, AgentRunStatus
from app.core.security import OperatorContext
from app.models.agent_runtime import AgentRunRecord
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
    ) -> None:
        self.session = session
        self.operator = operator
        self.repository = repository or AgentRuntimeRepository(session)

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
        if task.workflow_version != "new-agent-v1":
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
        )
        await self.repository.append_event(
            run.id,
            "run.created",
            {"phase": run.phase, "status": run.status, "workflow_version": run.workflow_version},
        )
        run = await self.repository.transition_run(
            run.id, requested_phase=AgentPhase.ACQUIRE_SCHOOL_LOCK
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

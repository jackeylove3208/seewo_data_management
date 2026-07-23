import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.observability import agent_observability
from app.agent_runtime.repository import AgentRuntimeRepository, SchoolLockConflict
from app.agent_runtime.service import AgentSupervisorService
from app.agent_runtime.state_machine import AgentRunStatus
from app.agent_runtime.task_service import (
    AgentConnectorCapabilityFailure,
    AgentTaskConflict,
    AgentTaskService,
)
from app.ai.conversation_agent import ConversationSupervisorAgent
from app.ai.providers.llm import HttpLLMProvider
from app.api.dependencies import get_operator_context, get_session
from app.core.security import OperatorContext
from app.governance.agent_governance import interpret_clarification
from app.local_sources.service import LocalSourceService
from app.models.agent_analysis import AgentApprovalGroupRecord, AgentClarificationRecord
from app.models.agent_runtime import AgentRunRecord, SchoolTaskLockRecord
from app.models.reconciliation import ReconciliationTask
from app.models.reporting import AgentReportRecord
from app.repositories.agent_governance import (
    AgentGovernanceRepository,
    GovernanceReplayConflict,
)
from app.schemas.agent_api import (
    AgentActiveLockResponse,
    AgentApprovalGroupView,
    AgentClarificationView,
    AgentCommandResponse,
    AgentConversationResponse,
    AgentEventPage,
    AgentHistoryItem,
    AgentHistoryPage,
    AgentIntentView,
    AgentInteractionResponse,
    AgentMessageRequest,
    AgentMessageResponse,
    AgentReportResponse,
    AgentRollbackPreviewResponse,
    AgentStartConfirmation,
    AgentTaskEventResponse,
    AgentTaskIntent,
    AgentTaskResponse,
    ApprovalDecisionRequest,
    ClarificationConfirmationRequest,
    ClarificationRequest,
)
from app.schemas.agent_conversation import ConversationAgentContext
from app.tasks.deletion_service import (
    TaskDeletionBlocked,
    TaskDeletionNotFound,
    TaskDeletionService,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _error(code: str, message: str, **details: object) -> dict[str, object]:
    return {"code": code, "message": message, **details}


def _require_enabled(request: Request) -> None:
    if not request.app.state.settings.new_agent_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_error("new_agent_disabled", "New Agent workflow is disabled"),
        )


@router.get("/active-lock", response_model=AgentActiveLockResponse)
async def get_active_agent_lock(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentActiveLockResponse:
    _require_enabled(request)
    lock = await session.scalar(
        select(SchoolTaskLockRecord).where(
            SchoolTaskLockRecord.tenant_id == operator.tenant_id,
            SchoolTaskLockRecord.active.is_(True),
        )
    )
    if lock is None:
        return AgentActiveLockResponse(active=False)
    return AgentActiveLockResponse(
        active=True,
        owner_task_id=lock.owner_task_id,
        owner_run_id=lock.owner_run_id,
        acquired_at=lock.acquired_at,
        heartbeat_at=lock.heartbeat_at,
    )


@router.post(
    "/conversations",
    response_model=AgentConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_conversation(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentConversationResponse:
    _require_enabled(request)
    conversation = await AgentRuntimeRepository(session).create_conversation(
        tenant_id=operator.tenant_id, created_by=operator.operator_id
    )
    return AgentConversationResponse(id=conversation.id, status="active")


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=AgentMessageResponse,
    response_model_exclude_none=True,
)
async def send_agent_message(
    conversation_id: UUID,
    body: AgentMessageRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentMessageResponse:
    _require_enabled(request)
    repository = AgentRuntimeRepository(session)
    conversation = await repository.get_active_conversation(
        conversation_id, tenant_id=operator.tenant_id
    )
    if conversation is None:
        raise HTTPException(404, detail=_error("conversation_not_found", "Conversation not found"))
    active = await session.scalar(
        select(AgentRunRecord).where(
            AgentRunRecord.conversation_id == conversation.id,
            AgentRunRecord.status.in_(
                ("pending", "running", "waiting_human", "blocked_model_error", "terminating")
            ),
        )
    )
    if active is not None:
        raise HTTPException(
            409,
            detail=_error("invalid_state", "Conversation is locked by an active task"),
        )
    sources = LocalSourceService(request.app.state.settings).list_sources()
    provider = getattr(
        request.app.state,
        "conversation_provider",
        HttpLLMProvider(settings=request.app.state.settings),
    )
    decision = await ConversationSupervisorAgent(provider).reply(
        ConversationAgentContext(
            conversation_id=conversation.id,
            tenant_id=operator.tenant_id,
            message=body.message,
            available_source_refs=tuple(source.source_ref for source in sources),
        )
    )
    intent = {
        "title": decision.title or "全校组织数据同步",
        "entity_types": [entity.value for entity in decision.entity_types],
        "source": (
            {"kind": "local", "source_ref": decision.source_ref}
            if decision.source_ref is not None
            else None
        ),
        "target": (
            {"kind": "local", "source_ref": decision.target_ref}
            if decision.target_ref is not None
            else None
        ),
        "decision_kind": decision.kind,
    }
    conversation.context = intent
    view = AgentIntentView.model_validate(intent)
    confirmation = None
    can_confirm = (
        decision.kind == "start_confirmation"
        and view.source is not None
        and view.target is not None
        and view.entity_types
    )
    if can_confirm:
        confirmation = AgentStartConfirmation(
            title=view.title,
            summary=decision.message_zh,
            entity_types=view.entity_types,
        )
    return AgentMessageResponse(
        message=decision.message_zh,
        intent=view,
        start_confirmation=confirmation,
    )


@router.post(
    "/conversations/{conversation_id}/tasks",
    response_model=AgentTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_conversation_agent_task(
    conversation_id: UUID,
    body: AgentTaskIntent,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> AgentTaskResponse:
    _require_enabled(request)
    del body
    conversation = await AgentRuntimeRepository(session).get_active_conversation(
        conversation_id, tenant_id=operator.tenant_id
    )
    if conversation is None:
        raise HTTPException(404, detail=_error("conversation_not_found", "Conversation not found"))
    try:
        intent = AgentTaskIntent.model_validate(
            {
                key: conversation.context.get(key)
                for key in ("title", "entity_types", "source", "target")
            }
        )
    except ValueError as error:
        raise HTTPException(
            409,
            detail=_error(
                "start_confirmation_missing", "Conversation has no confirmed Agent intent"
            ),
        ) from error
    if conversation.context.get("decision_kind") != "start_confirmation":
        raise HTTPException(
            409,
            detail=_error(
                "start_confirmation_missing", "Conversation has no confirmed Agent intent"
            ),
        )
    service = AgentTaskService(
        session, operator=operator, settings=request.app.state.settings
    )
    try:
        task, _run = await service.create(
            intent, idempotency_key=idempotency_key, conversation_id=conversation_id
        )
        return await _task_response(service, task.id)
    except SchoolLockConflict as error:
        raise HTTPException(
            409,
            detail=_error(
                "school_lock_conflict",
                "School already has an active Agent task",
                owner_task_id=str(error.owner_task_id),
            ),
        ) from error
    except AgentConnectorCapabilityFailure as error:
        raise HTTPException(
            422, detail=_error("connector_capability_failure", str(error))
        ) from error
    except (AgentTaskConflict, ValueError) as error:
        raise HTTPException(409, detail=_error("invalid_state", str(error))) from error
    except LookupError as error:
        raise HTTPException(404, detail=_error("resource_not_found", str(error))) from error


async def _task_response(
    service: AgentTaskService,
    task_id: UUID,
) -> AgentTaskResponse:
    task, run = await service.get(task_id)
    report = await service.session.scalar(
        select(AgentReportRecord).where(AgentReportRecord.task_id == task.id)
    )
    return AgentTaskResponse(
        id=task.id,
        workflow_version="new-agent-v1",
        task_kind=task.task_kind,
        parent_task_id=task.parent_task_id,
        phase=run.phase,
        status=run.status,
        title=task.title,
        report_id=report.id if report is not None else None,
        rollback_eligible=bool(report and report.rollback_eligible),
        deletion_eligible=report.deletion_eligible if report is not None else True,
    )


@router.post("/tasks", response_model=AgentTaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_manual_agent_task(
    body: AgentTaskIntent,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> AgentTaskResponse:
    _require_enabled(request)
    service = AgentTaskService(
        session, operator=operator, settings=request.app.state.settings
    )
    try:
        task, _run = await service.create(body, idempotency_key=idempotency_key)
        return await _task_response(service, task.id)
    except SchoolLockConflict as error:
        raise HTTPException(
            409,
            detail=_error(
                "school_lock_conflict",
                "School already has an active Agent task",
                owner_task_id=str(error.owner_task_id),
            ),
        ) from error
    except AgentTaskConflict as error:
        raise HTTPException(409, detail=_error("idempotency_conflict", str(error))) from error
    except AgentConnectorCapabilityFailure as error:
        raise HTTPException(
            422, detail=_error("connector_capability_failure", str(error))
        ) from error
    except LookupError as error:
        raise HTTPException(404, detail=_error("resource_not_found", str(error))) from error
    except ValueError as error:
        raise HTTPException(422, detail=_error("invalid_agent_intent", str(error))) from error


@router.get("/tasks/{task_id}", response_model=AgentTaskResponse)
async def get_agent_task(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentTaskResponse:
    _require_enabled(request)
    try:
        return await _task_response(AgentTaskService(session, operator=operator), task_id)
    except LookupError as error:
        raise HTTPException(404, detail=_error("agent_task_not_found", str(error))) from error


@router.get("/tasks/{task_id}/events", response_model=AgentEventPage)
async def get_agent_events(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    cursor: Annotated[str | None, Query()] = None,
) -> AgentEventPage:
    _require_enabled(request)
    service = AgentTaskService(session, operator=operator)
    try:
        _task, run = await service.get(task_id)
    except LookupError as error:
        raise HTTPException(404, detail=_error("agent_task_not_found", str(error))) from error
    try:
        after = int(cursor or "0")
    except ValueError as error:
        raise HTTPException(422, detail=_error("invalid_cursor", "Invalid event cursor")) from error
    events = await AgentRuntimeRepository(session).list_events(run.id, after_sequence=after)
    return AgentEventPage(
        cursor=str(events[-1].sequence if events else after),
        events=tuple(
            AgentTaskEventResponse(
                id=event.id,
                cursor=str(event.sequence),
                type=event.event_type,
                phase=event.payload.get("phase"),
                status=event.payload.get("status"),
                payload=event.payload,
                created_at=event.created_at,
            )
            for event in events
        ),
    )


@router.get("/tasks/{task_id}/interactions", response_model=AgentInteractionResponse)
async def get_agent_interactions(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentInteractionResponse:
    _require_enabled(request)
    try:
        _task, run = await AgentTaskService(session, operator=operator).get(task_id)
    except LookupError as error:
        raise HTTPException(404, detail=_error("agent_task_not_found", str(error))) from error
    groups = tuple(
        await session.scalars(
            select(AgentApprovalGroupRecord)
            .where(AgentApprovalGroupRecord.run_id == run.id)
            .order_by(AgentApprovalGroupRecord.created_at, AgentApprovalGroupRecord.id)
        )
    )
    clarifications = tuple(
        await session.scalars(
            select(AgentClarificationRecord)
            .where(AgentClarificationRecord.run_id == run.id)
            .order_by(AgentClarificationRecord.created_at, AgentClarificationRecord.id)
        )
    )
    return AgentInteractionResponse(
        approval_groups=tuple(
            AgentApprovalGroupView(
                id=item.id,
                status=item.status,
                issue_kind=item.issue_kind,
                entity_kind=item.entity_kind,
                operation=item.operation,
                item_count=len(item.finding_ids),
            )
            for item in groups
        ),
        clarifications=tuple(
            AgentClarificationView(
                id=item.id,
                status=item.status,
                masked_candidates=tuple(_sanitize_public(item.masked_candidates)),
                allowed_outcomes=tuple(item.allowed_outcomes),
            )
            for item in clarifications
        ),
    )


@router.get("/tasks/{task_id}/report", response_model=AgentReportResponse)
async def get_agent_report(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentReportResponse:
    _require_enabled(request)
    report = await session.scalar(
        select(AgentReportRecord).where(
            AgentReportRecord.task_id == task_id,
            AgentReportRecord.tenant_id == operator.tenant_id,
        )
    )
    if report is None:
        raise HTTPException(404, detail=_error("report_not_found", "Agent report not found"))
    return AgentReportResponse(
        id=report.id,
        task_id=report.task_id,
        kind=report.kind,
        terminal_state=report.terminal_state,
        facts=_sanitize_public(report.facts),
        content=_sanitize_public(report.content),
        rollback_eligible=report.rollback_eligible,
        deletion_eligible=report.deletion_eligible,
        created_at=report.created_at,
    )


@router.post("/tasks/{task_id}/terminate", response_model=AgentCommandResponse)
async def terminate_agent_task(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentCommandResponse:
    _require_enabled(request)
    service = AgentTaskService(session, operator=operator)
    try:
        _task, run = await service.get(task_id)
        terminated = await AgentSupervisorService(session, operator=operator).terminate(
            run_id=run.id, reason="operator_requested"
        )
        return AgentCommandResponse(status=terminated.status)
    except LookupError as error:
        raise HTTPException(404, detail=_error("agent_task_not_found", str(error))) from error


@router.get("/history", response_model=AgentHistoryPage)
async def get_agent_history(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    cursor: Annotated[str | None, Query()] = None,
) -> AgentHistoryPage:
    _require_enabled(request)
    del cursor
    rows = tuple(
        (
            await session.execute(
                select(ReconciliationTask, AgentRunRecord, AgentReportRecord)
                .join(AgentRunRecord, AgentRunRecord.task_id == ReconciliationTask.id)
                .outerjoin(AgentReportRecord, AgentReportRecord.task_id == ReconciliationTask.id)
                .where(
                    ReconciliationTask.tenant_id == operator.tenant_id,
                    ReconciliationTask.workflow_version == "new-agent-v1",
                )
                .order_by(ReconciliationTask.created_at.desc(), ReconciliationTask.id.desc())
            )
        ).all()
    )
    items: list[AgentHistoryItem] = []
    for task, run, report in rows:
        summary = report.facts.get("mutation_summary", {}) if report is not None else {}
        items.append(
            AgentHistoryItem(
                id=task.id,
                workflow_version="new-agent-v1",
                task_kind=task.task_kind,
                parent_task_id=task.parent_task_id,
                phase=run.phase,
                status=run.status,
                title=task.title,
                report_id=report.id if report is not None else None,
                created_at=task.created_at,
                completed_at=report.created_at if report is not None else None,
                issue_summary={
                    "total": len(report.facts.get("findings", [])) if report is not None else 0,
                    "excluded": (
                        len(report.facts.get("excluded_findings", []))
                        if report is not None
                        else 0
                    ),
                },
                operation_summary={
                    "succeeded": int(summary.get("succeeded", 0)),
                    "failed": int(summary.get("failed", 0)),
                    "blocked": int(summary.get("blocked", 0)),
                },
                rollback_eligible=report.rollback_eligible if report is not None else False,
                deletion_eligible=report.deletion_eligible if report is not None else True,
                entity_types=tuple(task.entity_types),
            )
        )
    return AgentHistoryPage(items=tuple(items), next_cursor=None)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_task(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> None:
    _require_enabled(request)
    try:
        await TaskDeletionService(session).delete(task_id, operator.tenant_id)
    except TaskDeletionNotFound as error:
        raise HTTPException(404, detail=_error("agent_task_not_found", str(error))) from error
    except TaskDeletionBlocked as error:
        raise HTTPException(409, detail=_error("immutable_history", str(error))) from error


@router.post(
    "/tasks/{task_id}/rollback-preview",
    response_model=AgentRollbackPreviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def preview_agent_rollback(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentRollbackPreviewResponse:
    _require_enabled(request)
    report = await session.scalar(
        select(AgentReportRecord).where(
            AgentReportRecord.task_id == task_id,
            AgentReportRecord.tenant_id == operator.tenant_id,
        )
    )
    if report is None or not report.rollback_eligible:
        raise HTTPException(
            409,
            detail=_error("rollback_not_eligible", "Task has no verified rollback evidence"),
        )
    target_version = report.facts.get("output_target_version_id")
    if not target_version:
        raise HTTPException(
            409,
            detail=_error("rollback_evidence_missing", "Rollback target version is missing"),
        )
    try:
        preview = await AgentReportingService(session).create_rollback_task(
            source_task_id=task_id,
            tenant_id=operator.tenant_id,
            requested_by=operator.operator_id,
            target_version_id=UUID(str(target_version)),
        )
    except ValueError as error:
        raise HTTPException(409, detail=_error("school_lock_conflict", str(error))) from error
    return AgentRollbackPreviewResponse(
        task_id=preview.task_id,
        source_task_id=task_id,
        target_version_id=preview.target_version_id,
        operation_count=len(preview.operations),
    )


@router.post("/rollback-tasks/{task_id}/confirm", response_model=AgentTaskResponse)
async def confirm_agent_rollback(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentTaskResponse:
    _require_enabled(request)
    try:
        await AgentSupervisorService(session, operator=operator).confirm_rollback(
            task_id=task_id
        )
        return await _task_response(AgentTaskService(session, operator=operator), task_id)
    except LookupError as error:
        raise HTTPException(404, detail=_error("agent_task_not_found", str(error))) from error
    except ValueError as error:
        raise HTTPException(409, detail=_error("invalid_state", str(error))) from error


@router.post("/rollback-tasks/{task_id}/reject", response_model=AgentTaskResponse)
async def reject_agent_rollback(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentTaskResponse:
    _require_enabled(request)
    service = AgentTaskService(session, operator=operator)
    try:
        task, run = await service.get(task_id)
        if task.task_kind != "rollback" or run.phase != "intent_confirmed":
            raise ValueError("rollback Agent task is not awaiting confirmation")
        await AgentSupervisorService(session, operator=operator).terminate(
            run_id=run.id, reason="rollback_rejected"
        )
        return await _task_response(service, task.id)
    except LookupError as error:
        raise HTTPException(404, detail=_error("agent_task_not_found", str(error))) from error
    except ValueError as error:
        raise HTTPException(409, detail=_error("invalid_state", str(error))) from error


async def _approval_group(
    session: AsyncSession, task_id: UUID, group_id: UUID, tenant_id: str
) -> AgentApprovalGroupRecord:
    record = await session.scalar(
        select(AgentApprovalGroupRecord).where(
            AgentApprovalGroupRecord.id == group_id,
            AgentApprovalGroupRecord.task_id == task_id,
            AgentApprovalGroupRecord.tenant_id == tenant_id,
        )
    )
    if record is None:
        raise HTTPException(404, detail=_error("approval_not_found", "Approval group not found"))
    return record


@router.post("/tasks/{task_id}/approval-groups/{group_id}/approve")
async def approve_agent_group(
    task_id: UUID,
    group_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> dict[str, str]:
    _require_enabled(request)
    group = await _approval_group(session, task_id, group_id, operator.tenant_id)
    try:
        decided = await AgentGovernanceRepository(session).decide_approval(
            group.id,
            membership_hash=group.membership_hash,
            approved=True,
            actor_id=operator.operator_id,
            reason="operator_approved",
        )
    except GovernanceReplayConflict as error:
        raise HTTPException(409, detail=_error("stale_version", str(error))) from error
    agent_observability.observe(
        "approval_decided",
        task_id=task_id,
        run_id=decided.run_id,
        approval_count=len(decided.finding_ids),
        outcome="approved",
    )
    await _resume_after_approvals(session, decided.run_id)
    return {"status": decided.status}


@router.post("/tasks/{task_id}/approval-groups/{group_id}/reject")
async def reject_agent_group(
    task_id: UUID,
    group_id: UUID,
    body: ApprovalDecisionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> dict[str, str]:
    _require_enabled(request)
    group = await _approval_group(session, task_id, group_id, operator.tenant_id)
    try:
        decided = await AgentGovernanceRepository(session).decide_approval(
            group.id,
            membership_hash=group.membership_hash,
            approved=False,
            actor_id=operator.operator_id,
            reason=body.reason or "operator_rejected",
        )
    except GovernanceReplayConflict as error:
        raise HTTPException(409, detail=_error("stale_version", str(error))) from error
    agent_observability.observe(
        "approval_decided",
        task_id=task_id,
        run_id=decided.run_id,
        approval_count=len(decided.finding_ids),
        outcome="rejected",
    )
    await _resume_after_approvals(session, decided.run_id)
    return {"status": decided.status}


@router.post("/tasks/{task_id}/clarification")
async def clarify_agent_conflict(
    task_id: UUID,
    body: ClarificationRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> dict[str, object]:
    _require_enabled(request)
    task, run = await AgentTaskService(session, operator=operator).get(task_id)
    record = await session.scalar(
        select(AgentClarificationRecord)
        .where(
            AgentClarificationRecord.run_id == run.id,
            AgentClarificationRecord.status == "pending",
        )
        .order_by(AgentClarificationRecord.created_at, AgentClarificationRecord.id)
    )
    if record is None:
        raise HTTPException(
            409,
            detail=_error("clarification_required", "No pending clarification"),
        )
    candidate_ids = tuple(
        UUID(str(item["id"])) for item in record.masked_candidates if "id" in item
    )
    try:
        decision = interpret_clarification(
            body.message,
            work_item_id=record.work_item_id,
            candidates=candidate_ids,
            allowed_outcomes=tuple(record.allowed_outcomes),
        )
        updated = await AgentGovernanceRepository(session).record_clarification_interpretation(
            record.id,
            original_text=body.message,
            interpretation={
                "outcome": decision.outcome,
                "candidate_id": str(decision.candidate_id) if decision.candidate_id else None,
            },
            actor_id=operator.operator_id,
        )
        await AgentRuntimeRepository(session).append_event(
            run.id,
            "clarification_decision_ready",
            {
                "decision_id": str(updated.id),
                "outcome": decision.outcome,
                "candidate_id": str(decision.candidate_id) if decision.candidate_id else None,
            },
        )
    except (ValueError, GovernanceReplayConflict) as error:
        raise HTTPException(409, detail=_error("invalid_state", str(error))) from error
    return {"decision_id": str(updated.id), "status": updated.status, "task_id": str(task.id)}


@router.post("/tasks/{task_id}/clarification/{decision_id}/confirm")
async def confirm_agent_clarification(
    task_id: UUID,
    decision_id: UUID,
    body: ClarificationConfirmationRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> dict[str, str]:
    _require_enabled(request)
    record = await session.scalar(
        select(AgentClarificationRecord).where(
            AgentClarificationRecord.id == decision_id,
            AgentClarificationRecord.task_id == task_id,
            AgentClarificationRecord.tenant_id == operator.tenant_id,
        )
    )
    if record is None:
        raise HTTPException(
            404,
            detail=_error("clarification_not_found", "Clarification not found"),
        )
    try:
        updated = await AgentGovernanceRepository(session).confirm_clarification(
            record.id, actor_id=operator.operator_id, confirmed=body.confirmed
        )
    except GovernanceReplayConflict as error:
        raise HTTPException(409, detail=_error("stale_version", str(error))) from error
    await _resume_after_clarifications(session, record.run_id)
    return {"status": updated.status}


async def _resume_after_approvals(session: AsyncSession, run_id: UUID) -> None:
    pending = await session.scalar(
        select(AgentApprovalGroupRecord.id).where(
            AgentApprovalGroupRecord.run_id == run_id,
            AgentApprovalGroupRecord.status == "pending",
        )
    )
    if pending is not None:
        return
    await _resume_waiting_run(
        session, run_id, expected_phase="aggregate_risk_and_approvals"
    )


async def _resume_after_clarifications(session: AsyncSession, run_id: UUID) -> None:
    unresolved = await session.scalar(
        select(AgentClarificationRecord.id).where(
            AgentClarificationRecord.run_id == run_id,
            AgentClarificationRecord.status.in_(("pending", "interpreted")),
        )
    )
    if unresolved is not None:
        return
    await _resume_waiting_run(
        session, run_id, expected_phase="clarify_identity_conflicts"
    )


async def _resume_waiting_run(
    session: AsyncSession, run_id: UUID, *, expected_phase: str
) -> None:
    repository = AgentRuntimeRepository(session)
    run = await repository.get_run(run_id, for_update=True)
    if run is None or run.phase != expected_phase or run.status != "waiting_human":
        return
    resumed = await repository.transition_run(
        run.id, requested_status=AgentRunStatus.RUNNING
    )
    await repository.append_event(
        run.id,
        "run.resumed",
        {"phase": resumed.phase, "status": resumed.status},
    )


_PHONE_PATTERN = re.compile(r"(?<!\d)1\d{10}(?!\d)")


def _sanitize_public(value: Any, *, field: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_public(item, field=str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_public(item, field=field) for item in value]
    if isinstance(value, str):
        if field in {"phone", "student_phone", "手机号", "电话"}:
            return f"***{value[-4:]}" if value else value
        return _PHONE_PATTERN.sub(lambda match: f"***{match.group(0)[-4:]}", value)
    return value

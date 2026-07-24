import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_graph.evidence import build_evidence_manifest
from app.agent_graph.governance_executors import (
    FrozenConflictDraft,
    GraphConflictInstructionExecutor,
    GraphConflictTools,
)
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.tools import GraphPhaseToolGateway
from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.observability import agent_observability
from app.agent_runtime.repository import AgentRuntimeRepository, SchoolLockConflict
from app.agent_runtime.service import AgentSupervisorService
from app.agent_runtime.state_machine import AgentPhase, AgentRunStatus
from app.agent_runtime.task_service import (
    AgentConnectorCapabilityFailure,
    AgentTaskConflict,
    AgentTaskService,
)
from app.ai.conversation_agent import (
    ConversationModelResponseError,
    ConversationSupervisorAgent,
)
from app.ai.graph_subagents import (
    GraphSkillInvocation,
    GraphSkillModelRunner,
    GraphSubAgentFailure,
)
from app.ai.providers.base import ModelProviderError
from app.ai.providers.llm import HttpLLMProvider
from app.ai.skills.contracts import ConflictInstructionInput
from app.api.dependencies import get_operator_context, get_session
from app.core.security import OperatorContext
from app.governance.agent_governance import interpret_clarification
from app.local_sources.service import LocalSourceService
from app.models.agent_analysis import (
    AgentApprovalGroupRecord,
    AgentClarificationRecord,
    AgentGovernanceOperationRecord,
    AgentModelBatchRecord,
)
from app.models.agent_graph import AgentGraphRunRecord, AgentHumanGateRecord
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
    AgentConversationCurrentResponse,
    AgentConversationMessageView,
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
from app.schemas.agent_graph_api import (
    AgentGraphGateDecisionRequest,
    AgentGraphGateDecisionResponse,
    AgentGraphHumanGateView,
    AgentGraphProgressResponse,
)
from app.tasks.deletion_service import (
    TaskDeletionBlocked,
    TaskDeletionNotFound,
    TaskDeletionService,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])

_GRAPH_STAGE_BY_NODE = {
    "inspect_sources": "data_ingestion",
    "normalize_input_batches": "data_ingestion",
    "validate_input_contract": "data_ingestion",
    "build_identity_index": "agent_analysis",
    "construct_identity_work": "agent_analysis",
    "analyze_actionable_batches": "agent_analysis",
    "repair_analysis_batch": "agent_analysis",
    "resolve_identity_conflicts": "agent_analysis",
    "aggregate_risk": "governance_execution",
    "wait_high_risk_approvals": "governance_execution",
    "compile_execution_plan": "governance_execution",
    "preflight_execution": "governance_execution",
    "execute_ready_operations": "governance_execution",
    "verify_operations": "governance_execution",
    "generate_terminal_report": "report_and_rollback",
    "termination_report": "report_and_rollback",
    "abnormal_input_report": "report_and_rollback",
    "load_verified_mutations": "report_and_rollback",
    "assess_restore_impact": "report_and_rollback",
    "wait_restore_conflicts": "report_and_rollback",
    "wait_rollback_approval": "report_and_rollback",
    "compile_restore_plan": "report_and_rollback",
    "preflight_restore": "report_and_rollback",
    "execute_restore_operations": "report_and_rollback",
    "verify_restore_operations": "report_and_rollback",
    "generate_rollback_report": "report_and_rollback",
    "terminal": "terminal",
}

_GRAPH_ACTION_LABELS = {
    "inspect_sources": "正在检查第三方与希沃数据来源",
    "normalize_input_batches": "正在分批理解并规范化组织数据",
    "validate_input_contract": "正在校验数据接入结果",
    "build_identity_index": "正在建立身份索引",
    "construct_identity_work": "正在构建对账工作项",
    "analyze_actionable_batches": "正在生成 AI 分析与治理方案",
    "resolve_identity_conflicts": "正在等待身份冲突说明",
    "wait_high_risk_approvals": "正在等待高风险操作审批",
    "compile_execution_plan": "正在编译治理执行计划",
    "execute_ready_operations": "正在执行并验证已批准操作",
    "generate_terminal_report": "正在生成任务报告",
    "load_verified_mutations": "正在读取可回滚执行事实",
    "wait_rollback_approval": "正在等待回滚确认",
    "execute_restore_operations": "正在执行并验证回滚",
    "generate_rollback_report": "正在生成回滚报告",
    "blocked_model_error": "AI 模型连续失败，等待终止任务",
    "terminal": "任务已结束",
}

_GRAPH_SUB_AGENT_LABELS = {
    "inspect_sources": "数据接入 Agent",
    "normalize_input_batches": "数据接入 Agent",
    "analyze_actionable_batches": "对账分析 Agent",
    "repair_analysis_batch": "对账分析 Agent",
    "resolve_identity_conflicts": "冲突解释 Agent",
    "wait_high_risk_approvals": "治理执行 Agent",
    "execute_ready_operations": "执行监督 Agent",
    "execute_remaining_independent": "执行监督 Agent",
    "generate_terminal_report": "报告 Agent",
    "assess_restore_impact": "回滚评估 Agent",
    "wait_restore_conflicts": "回滚评估 Agent",
    "execute_restore_operations": "回滚执行 Agent",
    "generate_rollback_report": "报告 Agent",
}


def _error(code: str, message: str, **details: object) -> dict[str, object]:
    return {"code": code, "message": message, **details}


def _graph_business_stage(node: str) -> str:
    return _GRAPH_STAGE_BY_NODE.get(node, "agent_analysis")


def _graph_action_label(node: str) -> str:
    return _GRAPH_ACTION_LABELS.get(node, "Agent 正在安全处理当前阶段")


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


@router.get(
    "/conversations/current",
    response_model=AgentConversationCurrentResponse | None,
)
async def get_current_agent_conversation(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentConversationCurrentResponse | None:
    _require_enabled(request)
    repository = AgentRuntimeRepository(session)
    conversation = await repository.get_current_conversation(
        tenant_id=operator.tenant_id,
        created_by=operator.operator_id,
    )
    if conversation is None:
        return None
    messages = await repository.list_conversation_messages(
        conversation_id=conversation.id,
        tenant_id=operator.tenant_id,
    )
    run = await session.scalar(
        select(AgentRunRecord)
        .where(
            AgentRunRecord.conversation_id == conversation.id,
            AgentRunRecord.tenant_id == operator.tenant_id,
        )
        .order_by(AgentRunRecord.created_at.desc(), AgentRunRecord.id.desc())
        .limit(1)
    )
    intent = AgentIntentView.model_validate(conversation.context) if conversation.context else None
    confirmation = None
    can_confirm = (
        (run is None or run.status in {"completed", "terminated", "failed"})
        and conversation.context.get("decision_kind") == "start_confirmation"
        and intent is not None
        and intent.source is not None
        and intent.target is not None
        and bool(intent.entity_types)
    )
    if can_confirm and intent is not None:
        latest_assistant_text = next(
            (
                message.text
                for message in reversed(messages)
                if message.role == "assistant" and message.kind != "error"
            ),
            intent.title,
        )
        confirmation = AgentStartConfirmation(
            title=intent.title,
            summary=latest_assistant_text,
            entity_types=intent.entity_types,
        )
    task = (
        await _task_response(AgentTaskService(session, operator=operator), run.task_id)
        if run is not None
        else None
    )
    return AgentConversationCurrentResponse(
        id=conversation.id,
        status="active",
        messages=tuple(
            AgentConversationMessageView(
                id=message.id,
                role=message.role,
                kind=message.kind,
                text=message.text,
                created_at=message.created_at,
            )
            for message in messages
        ),
        intent=intent,
        start_confirmation=confirmation,
        task=task,
    )


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
    await repository.append_conversation_message(
        conversation_id=conversation.id,
        tenant_id=operator.tenant_id,
        role="user",
        text=body.message,
    )
    sources = LocalSourceService(request.app.state.settings).list_sources()
    provider = getattr(
        request.app.state,
        "conversation_provider",
        HttpLLMProvider(settings=request.app.state.settings),
    )
    try:
        decision = await ConversationSupervisorAgent(provider).reply(
            ConversationAgentContext(
                conversation_id=conversation.id,
                tenant_id=operator.tenant_id,
                message=body.message,
                available_source_refs=tuple(source.source_ref for source in sources),
                current_intent=dict(conversation.context),
            )
        )
    except (ConversationModelResponseError, ModelProviderError) as error:
        safe_message = "对话模型暂时无法生成有效回复，请稍后重试。"
        await repository.append_conversation_message(
            conversation_id=conversation.id,
            tenant_id=operator.tenant_id,
            role="assistant",
            kind="error",
            text=safe_message,
        )
        await session.commit()
        raise HTTPException(
            502,
            detail=_error(
                "conversation_model_error",
                safe_message,
            ),
        ) from error
    previous_intent = dict(conversation.context)
    intent = {
        "title": decision.title or previous_intent.get("title") or "全校组织数据同步",
        "entity_types": (
            [entity.value for entity in decision.entity_types]
            if decision.entity_types
            else previous_intent.get("entity_types", [])
        ),
        "source": (
            {"kind": "local", "source_ref": decision.source_ref}
            if decision.source_ref is not None
            else previous_intent.get("source")
        ),
        "target": (
            {"kind": "local", "source_ref": decision.target_ref}
            if decision.target_ref is not None
            else previous_intent.get("target")
        ),
        "decision_kind": decision.kind,
    }
    conversation.context = intent
    await repository.append_conversation_message(
        conversation_id=conversation.id,
        tenant_id=operator.tenant_id,
        role="assistant",
        text=decision.message_zh,
    )
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
        workflow_version=task.workflow_version,
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


@router.get(
    "/tasks/{task_id}/graph",
    response_model=AgentGraphProgressResponse,
)
async def get_agent_graph_progress(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentGraphProgressResponse:
    _require_enabled(request)
    try:
        task, run = await AgentTaskService(session, operator=operator).get(task_id)
    except LookupError as error:
        raise HTTPException(
            404, detail=_error("agent_task_not_found", str(error))
        ) from error
    if task.workflow_version != "agent-graph-v1":
        raise HTTPException(
            409,
            detail=_error(
                "graph_not_available",
                "Task does not use the controlled Agent graph",
            ),
        )
    graph = await AgentGraphRepository(session).get_run_state_for_agent_run(run.id)
    if graph is None:
        raise HTTPException(
            409,
            detail=_error("graph_state_missing", "Agent graph state is missing"),
        )
    gates = tuple(
        await session.scalars(
            select(AgentHumanGateRecord)
            .where(AgentHumanGateRecord.graph_run_id == graph.id)
            .order_by(
                AgentHumanGateRecord.created_at,
                AgentHumanGateRecord.id,
            )
        )
    )
    progress_completed, progress_total = await _graph_progress_counts(
        session,
        run_id=run.id,
        current_node=graph.current_node,
        gates=gates,
    )
    terminal = run.status in {"completed", "terminated", "failed"}
    return AgentGraphProgressResponse(
        task_id=task.id,
        workflow_version="agent-graph-v1",
        graph_version=graph.graph_version,
        graph_cursor=graph.cursor,
        current_node=graph.current_node,
        business_stage=_graph_business_stage(graph.current_node),
        current_action_zh=_graph_action_label(graph.current_node),
        sub_agent_zh=_GRAPH_SUB_AGENT_LABELS.get(graph.current_node),
        progress_completed=progress_completed,
        progress_total=progress_total,
        status=run.status,
        can_terminate=not terminal,
        human_gates=tuple(
            AgentGraphHumanGateView(
                id=gate.id,
                kind=gate.gate_kind,
                status=gate.status,
                item_count=len(gate.member_ids),
            )
            for gate in gates
        ),
    )


async def _graph_progress_counts(
    session: AsyncSession,
    *,
    run_id: UUID,
    current_node: str,
    gates: tuple[AgentHumanGateRecord, ...],
) -> tuple[int | None, int | None]:
    pending_gates = tuple(gate for gate in gates if gate.status == "pending")
    if pending_gates:
        return 0, len(pending_gates)
    if current_node in {"analyze_actionable_batches", "repair_analysis_batch"}:
        total = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(AgentModelBatchRecord)
                    .where(AgentModelBatchRecord.run_id == run_id)
                )
            )
            or 0
        )
        completed = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(AgentModelBatchRecord)
                    .where(
                        AgentModelBatchRecord.run_id == run_id,
                        AgentModelBatchRecord.status == "completed",
                    )
                )
            )
            or 0
        )
        return completed, total
    if current_node in {
        "execute_ready_operations",
        "execute_remaining_independent",
        "verify_operations",
    }:
        total = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(AgentGovernanceOperationRecord)
                    .where(AgentGovernanceOperationRecord.run_id == run_id)
                )
            )
            or 0
        )
        completed = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(AgentGovernanceOperationRecord)
                    .where(
                        AgentGovernanceOperationRecord.run_id == run_id,
                        AgentGovernanceOperationRecord.status.in_(
                            ("succeeded", "failed", "blocked", "skipped")
                        ),
                    )
                )
            )
            or 0
        )
        return completed, total
    return None, None


@router.post(
    "/tasks/{task_id}/graph/gates/{gate_id}/decision",
    response_model=AgentGraphGateDecisionResponse,
)
async def decide_agent_graph_gate(
    task_id: UUID,
    gate_id: UUID,
    body: AgentGraphGateDecisionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentGraphGateDecisionResponse:
    _require_enabled(request)
    try:
        task, run = await AgentTaskService(session, operator=operator).get(task_id)
    except LookupError as error:
        raise HTTPException(
            404, detail=_error("agent_task_not_found", str(error))
        ) from error
    if task.workflow_version != "agent-graph-v1":
        raise HTTPException(
            409,
            detail=_error("graph_not_available", "Task does not use Agent graph"),
        )
    row = (
        await session.execute(
            select(AgentHumanGateRecord, AgentGraphRunRecord)
            .join(
                AgentGraphRunRecord,
                AgentGraphRunRecord.id == AgentHumanGateRecord.graph_run_id,
            )
            .where(
                AgentHumanGateRecord.id == gate_id,
                AgentGraphRunRecord.run_id == run.id,
                AgentGraphRunRecord.tenant_id == operator.tenant_id,
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            404, detail=_error("graph_gate_not_found", "Agent graph gate not found")
        )
    gate, graph = row
    if gate.status != "pending":
        raise HTTPException(
            409, detail=_error("graph_gate_already_decided", "Gate is already decided")
        )
    if gate.gate_kind == "identity_conflict":
        raise HTTPException(
            409,
            detail=_error(
                "clarification_dialogue_required",
                "Identity conflicts require operator dialogue and confirmation",
            ),
        )
    expected_gate_node = {
        "high_risk_approval": "wait_high_risk_approvals",
        "identity_conflict": "resolve_identity_conflicts",
        "rollback_conflict": "wait_restore_conflicts",
        "rollback_approval": "wait_rollback_approval",
        "cross_phase_replan": "wait_replan_confirmation",
    }.get(gate.gate_kind)
    gate_is_current = (
        gate.cursor == graph.cursor
        if gate.gate_kind == "termination_confirmation"
        else (
            expected_gate_node is not None
            and gate.cursor + 1 == graph.cursor
            and graph.current_node == expected_gate_node
        )
    )
    if not gate_is_current:
        raise HTTPException(
            409, detail=_error("stale_graph_gate", "Gate cursor is stale")
        )
    status_value = "approved" if body.decision == "approve" else "rejected"
    gate.status = status_value
    gate.decision = {
        "decision": body.decision,
        "reason": body.reason,
        "graph_cursor": graph.cursor,
    }
    gate.decided_by = operator.operator_id
    gate.decided_at = datetime.now(UTC)
    if gate.gate_kind == "termination_confirmation" and status_value == "approved":
        await AgentSupervisorService(session, operator=operator).terminate(
            run_id=run.id,
            reason="operator_confirmed",
        )
    if (
        status_value == "rejected"
        and gate.gate_kind
        in {"rollback_conflict", "rollback_approval", "cross_phase_replan"}
    ):
        graph.termination_requested = True
    if gate.gate_kind == "high_risk_approval":
        legacy_groups = tuple(
            await session.scalars(
                select(AgentApprovalGroupRecord).where(
                    AgentApprovalGroupRecord.run_id == run.id
                )
            )
        )
        matching_group = next(
            (
                group
                for group in legacy_groups
                if group.finding_ids == gate.member_ids
            ),
            None,
        )
        if matching_group is None:
            raise HTTPException(
                409,
                detail=_error(
                    "approval_fact_missing",
                    "Frozen approval group fact is missing",
                ),
            )
        matching_group.status = status_value
        matching_group.decided_by = operator.operator_id
        matching_group.decision_reason = (body.reason or "")[:1000]
        matching_group.decided_at = gate.decided_at
        matching_group.updated_at = gate.decided_at
    remaining_gate = await session.scalar(
        select(AgentHumanGateRecord.id).where(
            AgentHumanGateRecord.graph_run_id == graph.id,
            AgentHumanGateRecord.id != gate.id,
            AgentHumanGateRecord.gate_kind == gate.gate_kind,
            AgentHumanGateRecord.status == "pending",
        )
    )
    if remaining_gate is None and run.status == AgentRunStatus.WAITING_HUMAN.value:
        run.status = AgentRunStatus.RUNNING.value
        task.status = "running"
    await AgentRuntimeRepository(session).append_event(
        run.id,
        "graph.gate_decided",
        {
            "gate_id": str(gate.id),
            "gate_kind": gate.gate_kind,
            "status": status_value,
            "graph_cursor": graph.cursor,
        },
    )
    return AgentGraphGateDecisionResponse(
        gate_id=gate.id,
        status=status_value,
        graph_cursor=graph.cursor,
    )


@router.post(
    "/tasks/{task_id}/termination-preview",
    response_model=AgentGraphHumanGateView,
)
async def preview_agent_graph_termination(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentGraphHumanGateView:
    _require_enabled(request)
    try:
        task, run = await AgentTaskService(session, operator=operator).get(task_id)
    except LookupError as error:
        raise HTTPException(
            404, detail=_error("agent_task_not_found", str(error))
        ) from error
    if task.workflow_version != "agent-graph-v1":
        raise HTTPException(
            409,
            detail=_error(
                "graph_not_available",
                "Termination preview is available only for controlled Agent graph tasks",
            ),
        )
    if run.status in {"completed", "terminated", "failed"}:
        raise HTTPException(
            409,
            detail=_error("invalid_state", "Terminal Agent task cannot be terminated"),
        )
    graph = await AgentGraphRepository(session).get_run_state_for_agent_run(
        run.id,
        for_update=True,
    )
    if graph is None:
        raise HTTPException(
            409,
            detail=_error("graph_state_missing", "Agent graph state is missing"),
        )
    existing = await session.scalar(
        select(AgentHumanGateRecord).where(
            AgentHumanGateRecord.graph_run_id == graph.id,
            AgentHumanGateRecord.cursor == graph.cursor,
            AgentHumanGateRecord.gate_kind == "termination_confirmation",
            AgentHumanGateRecord.status == "pending",
        )
    )
    gate = existing or await AgentGraphRepository(session).record_human_gate(
        graph_run_id=graph.id,
        cursor=graph.cursor,
        gate_kind="termination_confirmation",
        member_ids=(str(task.id),),
        content_hash=(
            "sha256:"
            + sha256(
                f"{graph.id}:{graph.cursor}:termination".encode()
            ).hexdigest()
        ),
        status="pending",
    )
    return AgentGraphHumanGateView(
        id=gate.id,
        kind=gate.gate_kind,
        status=gate.status,
        item_count=len(gate.member_ids),
    )


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
        task, run = await service.get(task_id)
        if task.workflow_version == "agent-graph-v1":
            raise HTTPException(
                409,
                detail=_error(
                    "termination_confirmation_required",
                    "Create and approve a termination confirmation gate first",
                ),
            )
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
                    ReconciliationTask.workflow_version.in_(
                        ("new-agent-v1", "agent-graph-v1")
                    ),
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
                workflow_version=task.workflow_version,
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
    if task.workflow_version == "agent-graph-v1":
        graph = await AgentGraphRepository(session).get_run_state_for_agent_run(
            run.id,
            for_update=True,
        )
        if graph is None or graph.current_node != "resolve_identity_conflicts":
            raise HTTPException(
                409,
                detail=_error(
                    "invalid_state",
                    "Agent graph is not waiting for identity clarification",
                ),
            )
        gate = await session.scalar(
            select(AgentHumanGateRecord).where(
                AgentHumanGateRecord.graph_run_id == graph.id,
                AgentHumanGateRecord.gate_kind == "identity_conflict",
                AgentHumanGateRecord.status == "pending",
            )
        )
        if gate is None or str(record.id) not in gate.member_ids:
            raise HTTPException(
                409,
                detail=_error(
                    "stale_graph_gate",
                    "Frozen identity conflict gate is missing or stale",
                ),
            )
        resource_id = f"identity-conflict:{record.id}"
        action_id = f"interpret_identity_conflict:{record.id}"
        evidence_ref = f"{resource_id}:frozen"
        manifest = build_evidence_manifest(
            tenant_ref=f"tenant-ref:{operator.tenant_id}",
            task_id=str(task.id),
            run_id=str(run.id),
            graph_node=graph.current_node,
            action_id=action_id,
            resource_ids=(resource_id,),
            allowed_evidence_refs=(evidence_ref,),
        )
        await AgentGraphRepository(session).record_manifest(
            graph_run_id=graph.id,
            cursor=graph.cursor,
            graph_node=graph.current_node,
            action_id=action_id,
            manifest=manifest.model_dump(mode="json"),
            content_hash=manifest.content_hash,
            record_id=manifest.manifest_id,
        )
        tools = GraphConflictTools(
            task_id=task.id,
            run_id=run.id,
            tenant_id=operator.tenant_id,
            conflict=FrozenConflictDraft(
                conflict_id=record.id,
                work_item_id=record.work_item_id,
                masked_candidates=tuple(dict(item) for item in record.masked_candidates),
                allowed_outcomes=tuple(record.allowed_outcomes),
            ),
        )
        provider = getattr(
            request.app.state,
            "graph_skill_provider",
            HttpLLMProvider(settings=request.app.state.settings),
        )
        runner = GraphSkillModelRunner(
            session,
            provider=provider,
            tool_gateway=GraphPhaseToolGateway(
                session,
                operator=operator,
                tools=tools.handlers(),
            ),
            operator=operator,
            max_retries=request.app.state.settings.model_retry_attempts,
        )
        try:
            draft = await GraphConflictInstructionExecutor(
                runner=runner,
                tools=tools,
            ).run(
                GraphSkillInvocation(
                    task_id=task.id,
                    run_id=run.id,
                    graph_run_id=graph.id,
                    graph_node=graph.current_node,
                    graph_cursor=graph.cursor,
                    action_id=action_id,
                    evidence_manifest_id=manifest.manifest_id,
                    skill_name="resolve-human-conflict-instruction",
                    skill_version="1.0.0",
                    input_payload=ConflictInstructionInput(
                        task_id=task.id,
                        run_id=run.id,
                        phase=AgentPhase.CLARIFY_IDENTITY_CONFLICTS,
                        evidence_refs=(evidence_ref,),
                        conflict_id=record.id,
                        candidate_ids=candidate_ids,
                        operator_instruction=body.message,
                    ).model_dump(mode="json"),
                )
            )
        except GraphSubAgentFailure as error:
            raise HTTPException(
                503,
                detail=_error(
                    "agent_model_failure",
                    "AI 无法解释当前说明，请重试或终止任务。",
                ),
            ) from error
        if draft.decision == "leave_unresolved":
            return {
                "decision_id": str(record.id),
                "status": "pending",
                "task_id": str(task.id),
                "decision": draft.decision,
                "selected_candidate_id": None,
                "interpretation_zh": draft.interpretation_zh,
                "requires_second_confirmation": False,
            }
        outcome = (
            "use_candidate"
            if draft.decision == "select_candidate"
            else "target_extra"
        )
        updated = await AgentGovernanceRepository(
            session
        ).record_clarification_interpretation(
            record.id,
            original_text=body.message,
            interpretation={
                "outcome": outcome,
                "candidate_id": (
                    str(draft.selected_candidate_id)
                    if draft.selected_candidate_id
                    else None
                ),
                "interpretation_zh": draft.interpretation_zh,
                "model_decision": draft.decision,
            },
            actor_id=operator.operator_id,
        )
        await AgentRuntimeRepository(session).append_event(
            run.id,
            "clarification_decision_ready",
            {
                "decision_id": str(updated.id),
                "outcome": outcome,
                "candidate_id": (
                    str(draft.selected_candidate_id)
                    if draft.selected_candidate_id
                    else None
                ),
                "interpretation_zh": draft.interpretation_zh,
            },
        )
        return {
            "decision_id": str(updated.id),
            "status": updated.status,
            "task_id": str(task.id),
            "decision": draft.decision,
            "selected_candidate_id": (
                str(draft.selected_candidate_id)
                if draft.selected_candidate_id
                else None
            ),
            "interpretation_zh": draft.interpretation_zh,
            "requires_second_confirmation": True,
        }
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
    graph = await AgentGraphRepository(session).get_run_state_for_agent_run(
        run_id,
        for_update=True,
    )
    if graph is not None and graph.current_node == "resolve_identity_conflicts":
        gates = tuple(
            await session.scalars(
                select(AgentHumanGateRecord).where(
                    AgentHumanGateRecord.graph_run_id == graph.id,
                    AgentHumanGateRecord.gate_kind == "identity_conflict",
                    AgentHumanGateRecord.status == "pending",
                )
            )
        )
        decided_at = datetime.now(UTC)
        for gate in gates:
            gate.status = "approved"
            gate.decision = {
                "decision": "dialogue_completed",
                "graph_cursor": graph.cursor,
            }
            gate.decided_by = "operator_dialogue"
            gate.decided_at = decided_at
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

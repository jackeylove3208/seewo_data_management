import re
from asyncio import CancelledError
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_graph.evidence import build_evidence_manifest
from app.agent_graph.governance_executors import (
    FrozenConflictDraft,
    GraphConflictInstructionExecutor,
    GraphConflictTools,
)
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.tools import GraphPhaseToolGateway
from app.agent_reporting.rollback_cycles import (
    AgentRollbackCycleService,
    RollbackAlreadyPerformed,
    RollbackCycleChanged,
    is_fully_successful_sync,
)
from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.observability import agent_observability
from app.agent_runtime.repository import (
    AgentRuntimeRepository,
    ConversationResetConflict,
    SchoolLockConflict,
)
from app.agent_runtime.service import AgentSupervisorService
from app.agent_runtime.state_machine import AgentPhase, AgentRunStatus
from app.agent_runtime.task_service import (
    AgentConnectorCapabilityFailure,
    AgentTargetBaselineDrift,
    AgentTaskConflict,
    AgentTaskService,
)
from app.ai.conversation_agent import (
    ConversationModelResponseError,
    ConversationSupervisorAgent,
)
from app.ai.conversation_context import ConversationContextLimitError
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
    AgentFindingRecord,
    AgentFindingSolutionRecord,
    AgentGovernanceOperationRecord,
    AgentIdentityClaimRecord,
    AgentInputRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.models.agent_graph import AgentGraphRunRecord, AgentHumanGateRecord
from app.models.agent_runtime import (
    AgentConversationRecord,
    AgentRunRecord,
    SchoolTaskLockRecord,
)
from app.models.reconciliation import ReconciliationTask
from app.models.reporting import AgentReportRecord
from app.remote_sources.links import (
    RemoteSourceRegistrationError,
    extract_conversation_link,
    redact_conversation_links,
)
from app.remote_sources.repository import RemoteSourceRepository
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
    AgentLocalSourceView,
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
    StructuredClarificationSelectionRequest,
)
from app.schemas.agent_conversation import (
    ConversationAgentContext,
    ConversationDatabaseConnector,
    ConversationHistoryMessage,
    ConversationRemoteSource,
)
from app.schemas.agent_graph_api import (
    AgentGraphApprovalChangeView,
    AgentGraphApprovalItemView,
    AgentGraphClarificationSubmissionView,
    AgentGraphGateBatchDecisionRequest,
    AgentGraphGateBatchDecisionResponse,
    AgentGraphGateDecisionRequest,
    AgentGraphGateDecisionResponse,
    AgentGraphHumanGateView,
    AgentGraphIdentityConflictView,
    AgentGraphIdentityRecordView,
    AgentGraphProgressResponse,
)
from app.tasks.deletion_service import (
    TaskDeletionBlocked,
    TaskDeletionNotFound,
    TaskDeletionService,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])

_ACTIVE_CONVERSATION_RUN_STATUSES = (
    "pending",
    "running",
    "waiting_human",
    "blocked_model_error",
    "terminating",
)
_TERMINAL_CONVERSATION_RUN_STATUSES = ("completed", "terminated", "failed")
_CONVERSATION_MESSAGE_TOKEN_KEY = "_message_in_flight"
_CONVERSATION_MESSAGE_LEASE_DURATION = timedelta(minutes=15)


def _message_claim(token: str) -> dict[str, str]:
    return {
        "token": token,
        "claimed_at": datetime.now(UTC).isoformat(),
    }


def _message_claim_token(context: Mapping[str, Any]) -> str | None:
    claim = context.get(_CONVERSATION_MESSAGE_TOKEN_KEY)
    if not isinstance(claim, Mapping):
        return None
    token = claim.get("token")
    return token if isinstance(token, str) else None


def _message_claim_is_active(context: Mapping[str, Any]) -> bool:
    claim = context.get(_CONVERSATION_MESSAGE_TOKEN_KEY)
    if not isinstance(claim, Mapping):
        return False
    claimed_at = claim.get("claimed_at")
    if not isinstance(claimed_at, str) or _message_claim_token(context) is None:
        return False
    try:
        claimed_at_time = datetime.fromisoformat(claimed_at)
    except ValueError:
        return False
    if claimed_at_time.tzinfo is None:
        claimed_at_time = claimed_at_time.replace(tzinfo=UTC)
    return datetime.now(UTC) - claimed_at_time.astimezone(UTC) < (
        _CONVERSATION_MESSAGE_LEASE_DURATION
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

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


@router.get("/local-sources", response_model=tuple[AgentLocalSourceView, ...])
async def list_agent_local_sources(request: Request) -> tuple[AgentLocalSourceView, ...]:
    _require_enabled(request)
    return tuple(
        AgentLocalSourceView(
            source_ref=source.source_ref,
            kind="csv",
            writable_as_target=source.writable_as_target,
        )
        for source in LocalSourceService(request.app.state.settings).list_sources()
    )


_GRAPH_ACTION_LABELS = {
    "inspect_sources": "正在检查第三方与希沃数据来源",
    "normalize_input_batches": "正在分批理解并规范化组织数据",
    "validate_input_contract": "正在校验数据接入结果",
    "build_identity_index": "正在建立身份索引",
    "construct_identity_work": "正在构建对账工作项",
    "analyze_actionable_batches": "正在生成 AI 分析与治理方案",
    "resolve_identity_conflicts": "正在等待身份冲突说明",
    "wait_high_risk_approvals": "正在等待治理操作审核",
    "compile_execution_plan": "正在编译治理执行计划",
    "execute_ready_operations": "正在执行并验证已批准操作",
    "generate_terminal_report": "正在生成任务报告",
    "load_verified_mutations": "正在读取执行事实并比对当前目标数据",
    "assess_restore_impact": "正在判定可恢复、已恢复与冲突操作",
    "wait_restore_conflicts": "正在等待处理回滚数据冲突",
    "wait_rollback_approval": "正在等待确认回滚范围",
    "compile_restore_plan": "正在冻结回滚计划与数据比较哈希",
    "preflight_restore": "正在准备逐项回滚，每项写入前都会重新校验",
    "execute_restore_operations": "正在执行并验证回滚",
    "verify_restore_operations": "正在进入回滚结果汇总",
    "generate_rollback_report": "正在生成回滚报告",
    "blocked_model_error": "Agent 处理已安全暂停，等待终止任务",
    "terminal": "任务已结束",
}

_GRAPH_STAGE_BY_RUN_PHASE = {
    "intent_confirmed": "data_ingestion",
    "acquire_school_lock": "data_ingestion",
    "ingest_and_normalize": "data_ingestion",
    "build_identity_work": "agent_analysis",
    "analyze_batches": "agent_analysis",
    "clarify_identity_conflicts": "agent_analysis",
    "aggregate_risk_and_approvals": "governance_execution",
    "compile_execution_plan": "governance_execution",
    "execute_and_verify": "governance_execution",
    "generate_report": "report_and_rollback",
    "plan_restore": "report_and_rollback",
    "clarify_restore_conflicts": "report_and_rollback",
    "approve_restore": "report_and_rollback",
    "execute_restore": "report_and_rollback",
    "report_restore": "report_and_rollback",
    "terminal": "terminal",
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


def _graph_business_stage(node: str, run_phase: str | None = None) -> str:
    if node == "blocked_model_error" and run_phase is not None:
        return _GRAPH_STAGE_BY_RUN_PHASE.get(run_phase, "agent_analysis")
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
    conversation = await AgentRuntimeRepository(session).get_or_create_conversation(
        tenant_id=operator.tenant_id, created_by=operator.operator_id
    )
    return AgentConversationResponse(id=conversation.id, status="active")


@router.post(
    "/conversations/current/reset",
    response_model=AgentConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reset_current_agent_conversation(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
) -> AgentConversationResponse:
    _require_enabled(request)
    try:
        conversation = await AgentRuntimeRepository(session).reset_conversation(
            tenant_id=operator.tenant_id,
            created_by=operator.operator_id,
            idempotency_key=idempotency_key,
        )
    except ConversationResetConflict as error:
        raise HTTPException(
            409,
            detail=_error(
                "conversation_active_task",
                "当前学校仍有任务正在处理，请先完成或终止任务",
                owner_task_id=str(error.owner_task_id),
            ),
        ) from error
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
    remote_sources = await RemoteSourceRepository(session).list_for_conversation(
        tenant_id=operator.tenant_id,
        created_by=operator.operator_id,
        conversation_id=conversation.id,
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
    intent = (
        _intent_view(
            conversation.context,
            remote_origins={
                str(remote_source.id): remote_source.display_origin
                for remote_source in remote_sources
            },
        )
        if conversation.context
        else None
    )
    confirmation = None
    latest_run_is_active = (
        run is not None and run.status in _ACTIVE_CONVERSATION_RUN_STATUSES
    )
    terminal_run_superseded = bool(
        run is not None
        and run.status in _TERMINAL_CONVERSATION_RUN_STATUSES
        and any(
            message.role == "user"
            and _as_utc(message.created_at) > _as_utc(run.updated_at)
            for message in messages
        )
    )
    visible_run = None if terminal_run_superseded else run
    can_confirm = (
        not latest_run_is_active
        and not _message_claim_is_active(conversation.context)
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
        await _task_response(
            AgentTaskService(session, operator=operator),
            visible_run.task_id,
        )
        if visible_run is not None
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
        conversation_id,
        tenant_id=operator.tenant_id,
        for_update=True,
    )
    if conversation is None:
        raise HTTPException(404, detail=_error("conversation_not_found", "Conversation not found"))
    active = await session.scalar(
        select(AgentRunRecord).where(
            AgentRunRecord.conversation_id == conversation.id,
            AgentRunRecord.status.in_(_ACTIVE_CONVERSATION_RUN_STATUSES),
        )
    )
    if active is not None:
        raise HTTPException(
            409,
            detail=_error("invalid_state", "Conversation is locked by an active task"),
        )
    if _message_claim_is_active(conversation.context):
        raise HTTPException(
            409,
            detail=_error(
                "conversation_busy",
                "Conversation is already processing another message",
            ),
        )
    try:
        extracted_link = extract_conversation_link(body.message)
    except RemoteSourceRegistrationError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_error(error.code, error.safe_message),
        ) from error
    safe_message = (
        extracted_link.redacted_message
        if extracted_link is not None
        else redact_conversation_links(body.message)
    )
    remote_source_repository = RemoteSourceRepository(session)
    remote_csv_enabled = request.app.state.settings.conversation_remote_csv_enabled
    if extracted_link is not None and remote_csv_enabled:
        await remote_source_repository.register(
            tenant_id=operator.tenant_id,
            created_by=operator.operator_id,
            conversation_id=conversation.id,
            original_url=extracted_link.original_url,
            display_origin=extracted_link.display_origin,
        )
    remote_sources = (
        await remote_source_repository.list_for_conversation(
            tenant_id=operator.tenant_id,
            created_by=operator.operator_id,
            conversation_id=conversation.id,
        )
        if remote_csv_enabled
        else ()
    )
    message_token = str(uuid4())
    conversation.context = {
        **conversation.context,
        _CONVERSATION_MESSAGE_TOKEN_KEY: _message_claim(message_token),
    }
    await repository.append_conversation_message(
        conversation_id=conversation.id,
        tenant_id=operator.tenant_id,
        role="user",
        text=safe_message,
    )
    messages = await repository.list_conversation_messages(
        conversation_id=conversation.id,
        tenant_id=operator.tenant_id,
    )
    history = tuple(
        ConversationHistoryMessage(
            role=message.role,
            kind=message.kind,
            text=redact_conversation_links(message.text),
        )
        for message in messages
    )
    current_intent = {
        key: value
        for key, value in conversation.context.items()
        if key != _CONVERSATION_MESSAGE_TOKEN_KEY
    }
    sources = LocalSourceService(request.app.state.settings).list_sources()
    provider = getattr(
        request.app.state,
        "conversation_provider",
        HttpLLMProvider(settings=request.app.state.settings),
    )
    # Do not keep a database transaction or Conversation row lock open while the
    # model is working. The persisted user message remains part of full history.
    await session.commit()

    async def clear_message_claim() -> AgentConversationRecord | None:
        claimed_conversation = await repository.get_active_conversation(
            conversation_id,
            tenant_id=operator.tenant_id,
            for_update=True,
        )
        if (
            claimed_conversation is not None
            and _message_claim_token(claimed_conversation.context) == message_token
        ):
            claimed_conversation.context = {
                key: value
                for key, value in claimed_conversation.context.items()
                if key != _CONVERSATION_MESSAGE_TOKEN_KEY
            }
        return claimed_conversation

    try:
        decision = await ConversationSupervisorAgent(
            provider,
            max_context_tokens=(request.app.state.settings.conversation_context_max_tokens),
            reserved_output_tokens=(
                request.app.state.settings.conversation_context_reserved_output_tokens
            ),
        ).reply(
            ConversationAgentContext(
                conversation_id=conversation.id,
                tenant_id=operator.tenant_id,
                message=safe_message,
                history=history,
                available_source_refs=tuple(source.source_ref for source in sources),
                conversation_remote_csv_enabled=remote_csv_enabled,
                available_remote_sources=tuple(
                    ConversationRemoteSource(
                        remote_source_id=remote_source.id,
                        display_origin=remote_source.display_origin,
                    )
                    for remote_source in remote_sources
                ),
                available_database_connectors=tuple(
                    ConversationDatabaseConnector(
                        connector_id=connector_id,
                        dialect=configuration.dialect,
                        source_role=configuration.source_role,
                    )
                    for connector_id, configuration in sorted(
                        request.app.state.settings.database_connector_configurations.items()
                    )
                    if request.app.state.settings.agent_graph_sql_execution_enabled
                ),
                current_intent=current_intent,
            )
        )
    except ConversationContextLimitError as error:
        await clear_message_claim()
        await session.commit()
        raise HTTPException(
            409,
            detail=_error(
                "conversation_context_limit",
                "当前对话内容已达到模型处理上限，请开启新对话",
                estimated_tokens=error.estimated_tokens,
                available_tokens=error.available_tokens,
            ),
        ) from error
    except (ConversationModelResponseError, ModelProviderError) as error:
        await clear_message_claim()
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
    except CancelledError:
        await clear_message_claim()
        await session.commit()
        raise
    except Exception:
        await clear_message_claim()
        await session.commit()
        raise
    conversation = await repository.get_active_conversation(
        conversation_id,
        tenant_id=operator.tenant_id,
        for_update=True,
    )
    if conversation is None:
        raise HTTPException(
            404,
            detail=_error("conversation_not_found", "Conversation not found"),
        )
    active = await session.scalar(
        select(AgentRunRecord).where(
            AgentRunRecord.conversation_id == conversation.id,
            AgentRunRecord.status.in_(_ACTIVE_CONVERSATION_RUN_STATUSES),
        )
    )
    if active is not None:
        if _message_claim_token(conversation.context) == message_token:
            conversation.context = {
                key: value
                for key, value in conversation.context.items()
                if key != _CONVERSATION_MESSAGE_TOKEN_KEY
            }
        await repository.append_conversation_message(
            conversation_id=conversation.id,
            tenant_id=operator.tenant_id,
            role="assistant",
            kind="error",
            text="任务已经开始，本条新需求未应用。",
        )
        await session.commit()
        raise HTTPException(
            409,
            detail=_error("invalid_state", "Conversation is locked by an active task"),
        )
    if _message_claim_token(conversation.context) != message_token:
        raise HTTPException(
            409,
            detail=_error(
                "conversation_request_superseded",
                "Conversation changed while the model was processing",
            ),
        )
    previous_intent = {
        key: value
        for key, value in conversation.context.items()
        if key != _CONVERSATION_MESSAGE_TOKEN_KEY
    }
    intent = {
        "title": decision.title or previous_intent.get("title") or "全校组织数据同步",
        "entity_types": (
            [entity.value for entity in decision.entity_types]
            if decision.entity_types
            else previous_intent.get("entity_types", [])
        ),
        "source": (
            {
                "kind": "remote_csv",
                "remote_source_id": str(decision.remote_source_id),
            }
            if decision.remote_source_id is not None
            else {"kind": "local", "source_ref": decision.source_ref}
            if decision.source_ref is not None
            else {
                "kind": "database",
                "configuration_id": decision.source_configuration_id,
            }
            if decision.source_configuration_id is not None
            else previous_intent.get("source")
        ),
        "target": (
            {"kind": "local", "source_ref": decision.target_ref}
            if decision.target_ref is not None
            else {
                "kind": "database",
                "configuration_id": decision.target_configuration_id,
            }
            if decision.target_configuration_id is not None
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
    view = _intent_view(
        intent,
        remote_origins={
            str(remote_source.id): remote_source.display_origin
            for remote_source in remote_sources
        },
    )
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
        accepted_message=safe_message,
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
    accept_current_target_baseline: Annotated[
        bool,
        Header(alias="X-Accept-Current-Target-Baseline"),
    ] = False,
) -> AgentTaskResponse:
    _require_enabled(request)
    del body
    conversation = await AgentRuntimeRepository(session).get_active_conversation(
        conversation_id,
        tenant_id=operator.tenant_id,
        for_update=True,
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
    service = AgentTaskService(session, operator=operator, settings=request.app.state.settings)
    replayed_task = await session.scalar(
        select(ReconciliationTask)
        .join(AgentRunRecord, AgentRunRecord.task_id == ReconciliationTask.id)
        .where(
            ReconciliationTask.tenant_id == operator.tenant_id,
            ReconciliationTask.idempotency_key == idempotency_key,
            AgentRunRecord.conversation_id == conversation_id,
        )
    )
    if replayed_task is not None:
        return await _task_response(service, replayed_task.id)
    if conversation.context.get("decision_kind") != "start_confirmation":
        raise HTTPException(
            409,
            detail=_error(
                "start_confirmation_missing", "Conversation has no confirmed Agent intent"
            ),
        )
    try:
        task, _run = await service.create(
            intent,
            idempotency_key=idempotency_key,
            conversation_id=conversation_id,
            accept_current_target_baseline=accept_current_target_baseline,
        )
        conversation.context = {
            **{
                key: value
                for key, value in conversation.context.items()
                if key != _CONVERSATION_MESSAGE_TOKEN_KEY
            },
            "decision_kind": "task_started",
        }
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
    except AgentTargetBaselineDrift as error:
        raise HTTPException(
            409,
            detail=_error(
                "target_baseline_drift",
                str(error),
                source_ref=error.source_ref,
            ),
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
    rollback_blocked_reason = await AgentRollbackCycleService(
        service.session
    ).blocked_reason(task)
    report_rollback_eligible = bool(
        report
        and is_fully_successful_sync(
            task,
            report.terminal_state,
            report.facts,
        )
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
        rollback_eligible=bool(
            report_rollback_eligible and rollback_blocked_reason is None
        ),
        rollback_blocked_reason=rollback_blocked_reason,
        deletion_eligible=report.deletion_eligible if report is not None else True,
        error=dict(task.error) if isinstance(task.error, dict) else None,
    )


@router.post("/tasks", response_model=AgentTaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_manual_agent_task(
    body: AgentTaskIntent,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    accept_current_target_baseline: Annotated[
        bool,
        Header(alias="X-Accept-Current-Target-Baseline"),
    ] = False,
) -> AgentTaskResponse:
    _require_enabled(request)
    if (
        body.source.kind in {"database", "remote_csv"}
        or body.target.kind in {"database", "remote_csv"}
    ):
        raise HTTPException(
            422,
            detail=_error(
                "manual_csv_only",
                "手动同步只支持 CSV；SQL 数据源请通过新建对话发起。",
            ),
        )
    service = AgentTaskService(session, operator=operator, settings=request.app.state.settings)
    try:
        task, _run = await service.create(
            body,
            idempotency_key=idempotency_key,
            accept_current_target_baseline=accept_current_target_baseline,
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
    except AgentTaskConflict as error:
        raise HTTPException(409, detail=_error("idempotency_conflict", str(error))) from error
    except AgentConnectorCapabilityFailure as error:
        raise HTTPException(
            422, detail=_error("connector_capability_failure", str(error))
        ) from error
    except AgentTargetBaselineDrift as error:
        raise HTTPException(
            409,
            detail=_error(
                "target_baseline_drift",
                str(error),
                source_ref=error.source_ref,
            ),
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
        raise HTTPException(404, detail=_error("agent_task_not_found", str(error))) from error
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
    approval_groups = tuple(
        await session.scalars(
            select(AgentApprovalGroupRecord).where(AgentApprovalGroupRecord.run_id == run.id)
        )
    )
    approval_groups_by_members = {frozenset(group.finding_ids): group for group in approval_groups}
    approval_items_by_gate: dict[UUID, tuple[AgentGraphApprovalItemView, ...]] = {}
    identity_conflicts_by_gate: dict[UUID, tuple[AgentGraphIdentityConflictView, ...]] = {}
    for gate in gates:
        if gate.gate_kind == "high_risk_approval":
            approval_items_by_gate[gate.id] = await _graph_approval_items(
                session,
                finding_ids=tuple(gate.member_ids),
            )
        elif gate.gate_kind == "rollback_approval":
            approval_items_by_gate[gate.id] = await _graph_rollback_approval_items(
                session,
                task=task,
                operation_ids=tuple(gate.member_ids),
            )
        elif gate.gate_kind == "identity_conflict":
            identity_conflicts_by_gate[gate.id] = await _graph_identity_conflicts(
                session,
                run_id=run.id,
                clarification_ids=tuple(gate.member_ids),
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
        business_stage=_graph_business_stage(graph.current_node, run.phase),
        current_action_zh=_graph_current_action_label(
            graph.current_node,
            identity_conflicts_by_gate=identity_conflicts_by_gate,
        ),
        sub_agent_zh=_GRAPH_SUB_AGENT_LABELS.get(graph.current_node),
        progress_completed=progress_completed,
        progress_total=progress_total,
        status=run.status,
        can_terminate=not terminal,
        termination_requested=graph.termination_requested,
        human_gates=tuple(
            _graph_human_gate_view(
                gate,
                graph=graph,
                run=run,
                approval_group=approval_groups_by_members.get(frozenset(gate.member_ids)),
                items=approval_items_by_gate.get(gate.id, ()),
                conflicts=identity_conflicts_by_gate.get(gate.id, ()),
            )
            for gate in gates
        ),
    )


def _graph_current_action_label(
    current_node: str,
    *,
    identity_conflicts_by_gate: Mapping[
        UUID,
        tuple[AgentGraphIdentityConflictView, ...],
    ],
) -> str:
    if current_node != "resolve_identity_conflicts":
        return _graph_action_label(current_node)
    conflicts = tuple(
        conflict
        for gate_conflicts in identity_conflicts_by_gate.values()
        for conflict in gate_conflicts
    )
    if any(conflict.status == "interpreted" for conflict in conflicts):
        return "正在等待确认身份冲突解释"
    if any(
        conflict.status == "pending" and conflict.interpretation_zh
        for conflict in conflicts
    ):
        return "正在等待补充身份冲突说明"
    return _graph_action_label(current_node)


def _graph_human_gate_view(
    gate: AgentHumanGateRecord,
    *,
    graph: AgentGraphRunRecord,
    run: AgentRunRecord,
    approval_group: AgentApprovalGroupRecord | None = None,
    items: tuple[AgentGraphApprovalItemView, ...] = (),
    conflicts: tuple[AgentGraphIdentityConflictView, ...] = (),
) -> AgentGraphHumanGateView:
    summary_zh: str | None = None
    risk_reason_zh: str | None = None
    if gate.gate_kind == "high_risk_approval" and approval_group is not None:
        entity_label = {
            "student": "学生",
            "teacher": "教师",
            "department": "部门",
        }.get(approval_group.entity_kind, "组织")
        if _is_student_phone_approval_group(approval_group):
            summary_zh = f"修改 {len(gate.member_ids)} 条学生手机号"
            risk_reason_zh = "学生手机号属于高危隐私字段，本次操作会修改希沃目标中的手机号。"
        elif approval_group.operation == "delete":
            summary_zh = f"删除 {len(gate.member_ids)} 条{entity_label}记录"
            risk_reason_zh = "删除会永久移除希沃目标中的记录，治理后只能通过回滚任务恢复。"
        else:
            operation_label = {
                "create": "新增",
                "update": "修改",
                "retain": "保留",
                "skip": "跳过",
            }.get(approval_group.operation, "处理")
            summary_zh = f"{operation_label} {len(gate.member_ids)} 条{entity_label}记录"
            risk_reason_zh = (
                "该操作属于中风险变更，默认建议同意，但仍可逐项拒绝。"
                if approval_group.risk == "medium"
                else "该操作已被服务端风险策略判定为高风险。"
            )
    elif gate.gate_kind == "rollback_conflict":
        summary_zh = f"检测到 {len(gate.member_ids)} 条同步后数据已被修改"
        risk_reason_zh = (
            "这些操作涉及的当前数据已不再等于同步后的值。系统不会自动覆盖，"
            "请确认冲突后再决定是否继续回滚。"
        )
    elif gate.gate_kind == "rollback_approval":
        summary_zh = f"确认执行 {len(gate.member_ids)} 条回滚操作"
        risk_reason_zh = (
            "执行前仍会重新读取并校验当前目标数据；已经恢复的操作不会重复写入，"
            "发生新冲突的操作会安全跳过。"
        )
    actionable, unavailable_reason_zh = _graph_gate_actionability(
        gate,
        graph=graph,
        run=run,
    )
    if gate.gate_kind == "high_risk_approval" and not _approval_fact_is_complete(
        gate,
        approval_group=approval_group,
        items=items,
    ):
        actionable = False
        unavailable_reason_zh = "审批明细不完整，任务不能继续治理，请终止任务后重新发起。"
    elif gate.gate_kind == "rollback_approval" and not _rollback_approval_fact_is_complete(
        gate,
        items=items,
    ):
        actionable = False
        unavailable_reason_zh = "回滚审批明细不完整，任务不能继续，请终止任务后重新发起。"
    elif gate.gate_kind == "identity_conflict" and (
        len(conflicts) != len(gate.member_ids)
        or any(not conflict.evidence_complete for conflict in conflicts)
    ):
        actionable = False
        unavailable_reason_zh = "冲突明细不完整，不能要求操作人盲目判断，请终止任务后重新发起。"
    return AgentGraphHumanGateView(
        id=gate.id,
        kind=gate.gate_kind,
        status=gate.status,
        item_count=len(gate.member_ids),
        entity_kind=approval_group.entity_kind if approval_group else None,
        operation=approval_group.operation if approval_group else None,
        issue_kind=approval_group.issue_kind if approval_group else None,
        risk=approval_group.risk if approval_group else None,
        cursor=gate.cursor,
        membership_hash=(approval_group.membership_hash if approval_group is not None else None),
        member_decisions=(
            dict(gate.decision.get("member_decisions", {}))
            if isinstance(gate.decision, dict)
            else {}
        ),
        summary_zh=summary_zh,
        risk_reason_zh=risk_reason_zh,
        actionable=actionable,
        unavailable_reason_zh=unavailable_reason_zh,
        items=items,
        conflicts=conflicts,
    )


def _graph_gate_actionability(
    gate: AgentHumanGateRecord,
    *,
    graph: AgentGraphRunRecord,
    run: AgentRunRecord,
) -> tuple[bool, str | None]:
    if gate.status != "pending":
        return False, "该审批已经处理完成。"
    if run.status in {"completed", "terminated", "failed"}:
        return False, "任务已经结束或暂停，不能继续审批。"
    if gate.gate_kind == "termination_confirmation":
        if gate.cursor == graph.cursor:
            return True, None
        return False, "该终止确认已经过期。"
    if run.status == "blocked_model_error":
        return False, "任务已经结束或暂停，不能继续审批。"
    expected_gate_node = {
        "high_risk_approval": "wait_high_risk_approvals",
        "identity_conflict": "resolve_identity_conflicts",
        "rollback_conflict": "wait_restore_conflicts",
        "rollback_approval": "wait_rollback_approval",
        "cross_phase_replan": "wait_replan_confirmation",
    }.get(gate.gate_kind)
    if (
        expected_gate_node is None
        or gate.cursor + 1 != graph.cursor
        or graph.current_node != expected_gate_node
        or run.status != "waiting_human"
    ):
        return False, "该审批不属于任务当前执行节点，请刷新任务状态。"
    return True, None


def _approval_fact_is_complete(
    gate: AgentHumanGateRecord,
    *,
    approval_group: AgentApprovalGroupRecord | None,
    items: tuple[AgentGraphApprovalItemView, ...],
) -> bool:
    if (
        approval_group is None
        or approval_group.finding_ids != gate.member_ids
        or len(items) != len(gate.member_ids)
        or {str(item.finding_id) for item in items} != set(gate.member_ids)
    ):
        return False
    if _is_student_phone_approval_group(approval_group):
        return all(_student_phone_change_is_complete(item) for item in items)
    return True


def _rollback_approval_fact_is_complete(
    gate: AgentHumanGateRecord,
    *,
    items: tuple[AgentGraphApprovalItemView, ...],
) -> bool:
    if [str(item.finding_id) for item in items] != list(gate.member_ids):
        return False
    valid_operation_prefixes = (
        "恢复同步修改的",
        "删除同步新增的",
        "恢复同步删除的",
    )
    return all(
        item.source_locator
        and not item.source_locator.startswith("operation:")
        and item.operation_zh.startswith(valid_operation_prefixes)
        and bool(item.changes)
        for item in items
    )


def _is_student_phone_approval_group(
    approval_group: AgentApprovalGroupRecord,
) -> bool:
    return (
        approval_group.entity_kind == "student"
        and approval_group.operation == "update"
        and approval_group.risk == "high"
    )


def _student_phone_change_is_complete(item: AgentGraphApprovalItemView) -> bool:
    phone_changes = tuple(change for change in item.changes if change.field == "phone")
    if len(phone_changes) != 1:
        return False
    change = phone_changes[0]
    return (
        change.before is not None
        and change.after is not None
        and change.before != change.after
        and "****" in change.before
        and "****" in change.after
    )


async def _graph_identity_conflicts(
    session: AsyncSession,
    *,
    run_id: UUID,
    clarification_ids: tuple[str, ...],
) -> tuple[AgentGraphIdentityConflictView, ...]:
    try:
        parsed_ids = tuple(UUID(item) for item in clarification_ids)
    except ValueError:
        return ()
    if not parsed_ids:
        return ()
    clarifications = tuple(
        await session.scalars(
            select(AgentClarificationRecord).where(
                AgentClarificationRecord.id.in_(parsed_ids),
                AgentClarificationRecord.run_id == run_id,
            )
        )
    )
    clarifications_by_id = {record.id: record for record in clarifications}
    work_item_ids = {record.work_item_id for record in clarifications}
    work_items = tuple(
        await session.scalars(
            select(AgentWorkItemRecord).where(
                AgentWorkItemRecord.id.in_(work_item_ids),
                AgentWorkItemRecord.run_id == run_id,
            )
        )
    )
    work_items_by_id = {record.id: record for record in work_items}
    subject_ids = {record.subject_input_id for record in work_items}
    subjects = tuple(
        await session.scalars(
            select(AgentInputRecord).where(
                AgentInputRecord.id.in_(subject_ids),
                AgentInputRecord.run_id == run_id,
            )
        )
    )
    subjects_by_id = {record.id: record for record in subjects}
    views: list[AgentGraphIdentityConflictView] = []
    for clarification_id in parsed_ids:
        clarification = clarifications_by_id.get(clarification_id)
        if clarification is None:
            continue
        work_item = work_items_by_id.get(clarification.work_item_id)
        subject = (
            subjects_by_id.get(work_item.subject_input_id)
            if work_item is not None
            else None
        )
        if subject is None:
            continue
        interpretation = clarification.interpretation or {}
        interpretation_zh = interpretation.get("interpretation_zh")
        operator_submission: AgentGraphClarificationSubmissionView | None = None
        if interpretation.get("submission_source") == "structured_selection":
            decision = interpretation.get("model_decision")
            candidate_id = interpretation.get("candidate_id")
            try:
                parsed_candidate_id = UUID(str(candidate_id)) if candidate_id else None
            except ValueError:
                parsed_candidate_id = None
            if (
                decision in {"select_candidate", "treat_as_extra"}
                and isinstance(interpretation_zh, str)
                and interpretation_zh
            ):
                note = interpretation.get("note")
                operator_submission = AgentGraphClarificationSubmissionView(
                    decision=decision,
                    selected_candidate_id=parsed_candidate_id,
                    note=note if isinstance(note, str) and note else None,
                    interpretation_zh=interpretation_zh,
                    submitted_at=clarification.updated_at,
                    source="structured_selection",
                )
        subject_view = _graph_identity_record_view(
            {
                "entity_kind": subject.entity_kind,
                "category": subject.category,
                "name": subject.name,
                "number": subject.number,
                "class_name": subject.class_name,
                "phone": subject.phone,
                "email": subject.email,
            }
        )
        candidate_views = tuple(
            _graph_identity_record_view(
                candidate,
                default_entity_kind=subject.entity_kind,
            )
            for candidate in clarification.masked_candidates
        )
        views.append(
            AgentGraphIdentityConflictView(
                clarification_id=clarification.id,
                status=clarification.status,
                summary_zh="唯一身份字段命中了多个第三方权威候选，Agent 无法安全选择。",
                subject=subject_view,
                candidates=candidate_views,
                allowed_outcomes=tuple(clarification.allowed_outcomes),
                interpretation_zh=(
                    str(interpretation_zh)
                    if isinstance(interpretation_zh, str) and interpretation_zh
                    else None
                ),
                operator_submission=operator_submission,
                evidence_complete=_identity_conflict_evidence_is_complete(
                    clarification.masked_candidates,
                    subject=subject_view,
                    candidates=candidate_views,
                ),
            )
        )
    return tuple(views)


def _graph_identity_record_view(
    values: Mapping[str, object],
    *,
    default_entity_kind: str | None = None,
) -> AgentGraphIdentityRecordView:
    def text_value(key: str) -> str | None:
        value = values.get(key)
        return str(value) if value is not None and str(value) else None

    candidate_id_value = text_value("id")
    try:
        candidate_id = UUID(candidate_id_value) if candidate_id_value else None
    except ValueError:
        candidate_id = None
    return AgentGraphIdentityRecordView(
        candidate_id=candidate_id,
        entity_kind=text_value("entity_kind") or default_entity_kind,
        category=text_value("category"),
        name=text_value("name"),
        number=text_value("number"),
        class_name=text_value("class_name"),
        phone_masked=_mask_graph_phone(text_value("phone")),
        email_masked=_mask_graph_email(text_value("email")),
    )


def _mask_graph_phone(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("token:"):
        return "已保护"
    digits = "".join(character for character in value if character.isdigit())
    return f"***{digits[-4:]}" if len(digits) >= 4 else "已保护"


def _mask_graph_email(value: str | None) -> str | None:
    if value is None:
        return None
    if "@" not in value:
        return "已保护"
    local_part, domain = value.rsplit("@", 1)
    if not local_part or not domain:
        return "已保护"
    return f"{local_part[0]}***@{domain}"


def _identity_conflict_evidence_is_complete(
    frozen_candidates: list[dict[str, Any]],
    *,
    subject: AgentGraphIdentityRecordView,
    candidates: tuple[AgentGraphIdentityRecordView, ...],
) -> bool:
    if len(frozen_candidates) < 2 or len(candidates) != len(frozen_candidates):
        return False
    candidate_ids: set[UUID] = set()
    for frozen, candidate in zip(frozen_candidates, candidates, strict=True):
        try:
            candidate_id = UUID(str(frozen["id"]))
        except (KeyError, TypeError, ValueError):
            return False
        if candidate_id in candidate_ids or not _identity_record_has_evidence(candidate):
            return False
        candidate_ids.add(candidate_id)
    return _identity_record_has_evidence(subject)


def _identity_record_has_evidence(record: AgentGraphIdentityRecordView) -> bool:
    has_kind = bool(record.entity_kind or record.category)
    has_identity = any(
        (
            record.name,
            record.number,
            record.class_name,
            record.phone_masked,
            record.email_masked,
        )
    )
    return has_kind and has_identity


def _sanitized_frozen_candidate(candidate: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": candidate.get("id"),
        "entity_kind": candidate.get("entity_kind"),
        "category": candidate.get("category"),
        "name": candidate.get("name"),
        "number": candidate.get("number"),
        "class_name": candidate.get("class_name"),
        "phone": _mask_graph_phone(
            str(candidate["phone"]) if candidate.get("phone") is not None else None
        ),
        "email": _mask_graph_email(
            str(candidate["email"]) if candidate.get("email") is not None else None
        ),
    }


async def _graph_approval_items(
    session: AsyncSession,
    *,
    finding_ids: tuple[str, ...],
) -> tuple[AgentGraphApprovalItemView, ...]:
    parsed_ids = tuple(UUID(item) for item in finding_ids)
    if not parsed_ids:
        return ()
    rows = tuple(
        await session.execute(
            select(
                AgentFindingRecord,
                AgentWorkItemRecord,
                AgentInputRecord,
                AgentFindingSolutionRecord,
            )
            .join(
                AgentWorkItemRecord,
                AgentWorkItemRecord.id == AgentFindingRecord.work_item_id,
            )
            .join(
                AgentInputRecord,
                AgentInputRecord.id == AgentWorkItemRecord.subject_input_id,
            )
            .join(
                AgentFindingSolutionRecord,
                (AgentFindingSolutionRecord.finding_id == AgentFindingRecord.id)
                & AgentFindingSolutionRecord.recommended.is_(True),
            )
            .where(AgentFindingRecord.id.in_(parsed_ids))
        )
    )
    field_difference_work_item_ids = tuple(
        work_item.id
        for _finding, work_item, _subject, _solution in rows
        if work_item.kind == "field_difference"
    )
    claims = (
        tuple(
            await session.scalars(
                select(AgentIdentityClaimRecord).where(
                    AgentIdentityClaimRecord.work_item_id.in_(field_difference_work_item_ids)
                )
            )
        )
        if field_difference_work_item_ids
        else ()
    )
    claims_by_work_item = {claim.work_item_id: claim for claim in claims}
    claimed_input_ids = {
        input_id
        for claim in claims
        for input_id in (claim.authority_input_id, claim.target_input_id)
    }
    claimed_inputs = (
        tuple(
            await session.scalars(
                select(AgentInputRecord).where(AgentInputRecord.id.in_(claimed_input_ids))
            )
        )
        if claimed_input_ids
        else ()
    )
    claimed_inputs_by_id = {input_record.id: input_record for input_record in claimed_inputs}
    by_finding: dict[UUID, AgentGraphApprovalItemView] = {}
    for finding, work_item, subject, solution in rows:
        display_record = subject
        changes: tuple[AgentGraphApprovalChangeView, ...] = ()
        if work_item.kind == "field_difference":
            claim = claims_by_work_item.get(work_item.id)
            if claim is not None:
                authority = claimed_inputs_by_id.get(claim.authority_input_id)
                target = claimed_inputs_by_id.get(claim.target_input_id)
                if authority is not None and target is not None:
                    display_record = target
                    changes = _graph_approval_changes(target, authority)
        by_finding[finding.id] = AgentGraphApprovalItemView(
            finding_id=finding.id,
            entity_kind=work_item.entity_kind,
            entity_name=display_record.name,
            entity_number=display_record.number,
            class_name=display_record.class_name,
            source_locator=display_record.stable_locator,
            source_row_number=display_record.raw_row_number,
            operation_zh=_graph_operation_label(
                solution.operation,
                work_item.entity_kind,
            ),
            issue_zh=finding.category_zh,
            analysis_zh=str(_sanitize_public(finding.analysis_zh)),
            solution_zh=str(_sanitize_public(solution.solution_zh)),
            changes=changes,
        )
    return tuple(by_finding[finding_id] for finding_id in parsed_ids if finding_id in by_finding)


async def _graph_rollback_approval_items(
    session: AsyncSession,
    *,
    task: ReconciliationTask,
    operation_ids: tuple[str, ...],
) -> tuple[AgentGraphApprovalItemView, ...]:
    parsed_ids = tuple(UUID(item) for item in operation_ids)
    if not parsed_ids:
        return ()
    operation_rows = tuple(
        await session.scalars(
            select(AgentGovernanceOperationRecord).where(
                AgentGovernanceOperationRecord.id.in_(parsed_ids)
            )
        )
    )
    operation_rows_by_id = {row.id: row for row in operation_rows}
    finding_ids = tuple(dict.fromkeys(row.finding_id for row in operation_rows))
    source_items = (
        await _graph_approval_items(
            session,
            finding_ids=tuple(str(finding_id) for finding_id in finding_ids),
        )
        if finding_ids
        else ()
    )
    source_items_by_finding = {item.finding_id: item for item in source_items}
    intent = task.agent_intent if isinstance(task.agent_intent, dict) else {}
    mutations_by_id = {
        UUID(str(item["id"])): dict(item)
        for item in intent.get("operations", [])
        if isinstance(item, dict) and item.get("id")
    }
    items: list[AgentGraphApprovalItemView] = []
    for operation_id in parsed_ids:
        operation_row = operation_rows_by_id.get(operation_id)
        source_item = (
            source_items_by_finding.get(operation_row.finding_id)
            if operation_row is not None
            else None
        )
        mutation = mutations_by_id.get(operation_id)
        if mutation is None and operation_row is not None:
            mutation = {
                "id": str(operation_row.id),
                "operation": operation_row.operation_type,
                "entity_kind": operation_row.entity_kind,
                "target_source_identifier": operation_row.target_source_identifier,
                "before": operation_row.before,
                "after": operation_row.actual_after or operation_row.after,
            }
        if mutation is None:
            continue
        items.append(
            _graph_rollback_approval_item(
                operation_id=operation_id,
                mutation=mutation,
                source_item=source_item,
            )
        )
    return tuple(items)


def _graph_rollback_approval_item(
    *,
    operation_id: UUID,
    mutation: Mapping[str, Any],
    source_item: AgentGraphApprovalItemView | None,
) -> AgentGraphApprovalItemView:
    original_operation = str(mutation.get("operation", "update"))
    entity_kind = str(
        mutation.get("entity_kind")
        or (source_item.entity_kind if source_item is not None else "record")
    )
    before = _graph_fact_mapping(mutation.get("before"))
    after = _graph_fact_mapping(mutation.get("actual_after") or mutation.get("after"))
    current, restored = _graph_rollback_values(
        operation=original_operation,
        before=before,
        after=after,
    )
    source_locator = str(
        mutation.get("target_source_identifier")
        or (source_item.source_locator if source_item is not None else f"operation:{operation_id}")
    )
    entity_name = _graph_fact_text(after, before, field="name")
    entity_number = _graph_fact_text(after, before, field="number")
    class_name = _graph_fact_text(after, before, field="class_name")
    source_row_number = source_item.source_row_number if source_item is not None else None
    if source_row_number is None:
        match = re.fullmatch(r"csv:(\d+)", source_locator)
        source_row_number = int(match.group(1)) if match is not None else None
    entity_label = {
        "student": "学生",
        "teacher": "教师",
        "department": "部门",
    }.get(entity_kind, "组织")
    operation_label = {
        "update": f"恢复同步修改的{entity_label}记录",
        "create": f"删除同步新增的{entity_label}记录",
        "delete": f"恢复同步删除的{entity_label}记录",
    }.get(original_operation, f"恢复同步处理的{entity_label}记录")
    return AgentGraphApprovalItemView(
        finding_id=operation_id,
        entity_kind=entity_kind,
        entity_name=entity_name or (source_item.entity_name if source_item is not None else None),
        entity_number=(
            entity_number or (source_item.entity_number if source_item is not None else None)
        ),
        class_name=class_name or (source_item.class_name if source_item is not None else None),
        source_locator=source_locator,
        source_row_number=source_row_number,
        operation_zh=operation_label,
        issue_zh="已验证同步操作",
        analysis_zh="该记录属于本次冻结的回滚范围。",
        solution_zh=f"将按同步前事实{operation_label}。",
        changes=_graph_rollback_changes(current=current, restored=restored),
    )


def _graph_fact_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _graph_fact_text(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any],
    *,
    field: str,
) -> str | None:
    value = primary.get(field)
    if value is None:
        value = fallback.get(field)
    return str(value) if value is not None else None


def _graph_rollback_values(
    *,
    operation: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if operation == "create":
        return after, {}
    if operation == "delete":
        return {}, before
    return after, before


def _graph_rollback_changes(
    *,
    current: Mapping[str, Any],
    restored: Mapping[str, Any],
) -> tuple[AgentGraphApprovalChangeView, ...]:
    field_labels = {
        "category": "类别",
        "name": "姓名",
        "number": "编号",
        "class_name": "班级",
        "phone": "手机号",
        "email": "邮箱",
    }
    fields = tuple(
        field
        for field in dict.fromkeys((*field_labels, *current, *restored))
        if field not in {"source_id", "entity_type"}
    )
    return tuple(
        AgentGraphApprovalChangeView(
            field=field,
            field_zh=field_labels.get(field, field),
            before=_graph_public_value(current.get(field), field=field),
            after=_graph_public_value(restored.get(field), field=field),
        )
        for field in fields
        if current.get(field) != restored.get(field)
    )


def _graph_approval_changes(
    target: AgentInputRecord,
    authority: AgentInputRecord,
) -> tuple[AgentGraphApprovalChangeView, ...]:
    field_labels = {
        "category": "类别",
        "name": "姓名",
        "number": "编号",
        "class_name": "班级",
        "phone": "手机号",
        "email": "邮箱",
    }
    changes: list[AgentGraphApprovalChangeView] = []
    for field, field_zh in field_labels.items():
        before = getattr(target, field)
        after = getattr(authority, field)
        if before == after:
            continue
        changes.append(
            AgentGraphApprovalChangeView(
                field=field,
                field_zh=field_zh,
                before=_graph_public_value(before, field=field),
                after=_graph_public_value(after, field=field),
            )
        )
    return tuple(changes)


def _graph_public_value(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    text = str(value)
    if field == "phone":
        if len(text) >= 7:
            return f"{text[:3]}****{text[-4:]}"
        return "手机号（已脱敏）"
    if field == "email":
        local, separator, domain = text.partition("@")
        if separator:
            return f"{local[:1]}***@{domain}"
        return "邮箱（已脱敏）"
    return str(_sanitize_public(text, field=field))


def _graph_operation_label(operation: str, entity_kind: str) -> str:
    entity_label = {
        "student": "学生",
        "teacher": "教师",
        "department": "部门",
    }.get(entity_kind, "组织")
    operation_label = {
        "create": "新增",
        "update": "修改",
        "delete": "删除",
        "retain": "保留",
        "skip": "跳过",
    }.get(operation, "处理")
    return f"{operation_label}希沃中的{entity_label}记录"


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
    "/tasks/{task_id}/graph/gates/decisions",
    response_model=AgentGraphGateBatchDecisionResponse,
)
async def decide_agent_graph_gates(
    task_id: UUID,
    body: AgentGraphGateBatchDecisionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AgentGraphGateBatchDecisionResponse:
    gate_ids = [decision.gate_id for decision in body.decisions]
    if len(set(gate_ids)) != len(gate_ids):
        raise HTTPException(
            422,
            detail=_error(
                "duplicate_graph_gate",
                "A gate can only be decided once per batch",
            ),
        )
    decisions: list[AgentGraphGateDecisionResponse] = []
    for decision in body.decisions:
        decisions.append(
            await decide_agent_graph_gate(
                task_id=task_id,
                gate_id=decision.gate_id,
                body=AgentGraphGateDecisionRequest.model_validate(
                    decision.model_dump(exclude={"gate_id"})
                ),
                request=request,
                session=session,
                operator=operator,
            )
        )
    return AgentGraphGateBatchDecisionResponse(decisions=tuple(decisions))


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
        raise HTTPException(404, detail=_error("agent_task_not_found", str(error))) from error
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
    locked_run = await session.scalar(
        select(AgentRunRecord).where(AgentRunRecord.id == run.id).with_for_update()
    )
    if locked_run is None:
        raise HTTPException(
            409,
            detail=_error("graph_state_missing", "Agent run state is missing"),
        )
    run = locked_run
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
    actionable, _unavailable_reason = _graph_gate_actionability(
        gate,
        graph=graph,
        run=run,
    )
    if not actionable:
        raise HTTPException(409, detail=_error("stale_graph_gate", "Gate cursor is stale"))
    matching_group: AgentApprovalGroupRecord | None = None
    if gate.gate_kind == "high_risk_approval":
        legacy_groups = tuple(
            await session.scalars(
                select(AgentApprovalGroupRecord).where(AgentApprovalGroupRecord.run_id == run.id)
            )
        )
        matching_group = next(
            (group for group in legacy_groups if group.finding_ids == gate.member_ids),
            None,
        )
        approval_items = await _graph_approval_items(
            session,
            finding_ids=tuple(gate.member_ids),
        )
        if not _approval_fact_is_complete(
            gate,
            approval_group=matching_group,
            items=approval_items,
        ):
            raise HTTPException(
                409,
                detail=_error(
                    "approval_fact_missing",
                    "Frozen approval group detail is incomplete",
                ),
            )
    elif gate.gate_kind == "rollback_approval":
        approval_items = await _graph_rollback_approval_items(
            session,
            task=task,
            operation_ids=tuple(gate.member_ids),
        )
        if not _rollback_approval_fact_is_complete(gate, items=approval_items):
            raise HTTPException(
                409,
                detail=_error(
                    "approval_fact_missing",
                    "Frozen rollback approval detail is incomplete",
                ),
            )
    approved_member_ids: tuple[str, ...] = ()
    rejected_member_ids: tuple[str, ...] = ()
    if gate.gate_kind == "high_risk_approval":
        assert matching_group is not None
        if body.membership_hash is not None:
            if (
                body.membership_hash != matching_group.membership_hash
                or body.graph_cursor != graph.cursor
            ):
                raise HTTPException(
                    409,
                    detail=_error(
                        "stale_graph_gate",
                        "Gate cursor or membership is stale",
                    ),
                )
            approved_member_ids = tuple(str(item) for item in body.approved_finding_ids)
            rejected_member_ids = tuple(str(item) for item in body.rejected_finding_ids)
            approved_set = set(approved_member_ids)
            rejected_set = set(rejected_member_ids)
            if (
                approved_set.intersection(rejected_set)
                or approved_set.union(rejected_set) != set(gate.member_ids)
                or len(approved_set) != len(approved_member_ids)
                or len(rejected_set) != len(rejected_member_ids)
            ):
                raise HTTPException(
                    422,
                    detail=_error(
                        "invalid_member_partition",
                        "Review decision must partition every frozen finding",
                    ),
                )
        elif body.decision == "approve":
            approved_member_ids = tuple(gate.member_ids)
        else:
            rejected_member_ids = tuple(gate.member_ids)
    status_value = (
        "approved"
        if gate.gate_kind == "high_risk_approval" and approved_member_ids
        else "approved"
        if body.decision == "approve" and gate.gate_kind != "high_risk_approval"
        else "rejected"
    )
    gate.status = status_value
    gate.decision = {
        "decision": body.decision,
        "reason": body.reason,
        "graph_cursor": graph.cursor,
        "membership_hash": (matching_group.membership_hash if matching_group is not None else None),
        "approved_finding_ids": list(approved_member_ids),
        "rejected_finding_ids": list(rejected_member_ids),
        "member_decisions": {
            **{item: "approved" for item in approved_member_ids},
            **{item: "rejected" for item in rejected_member_ids},
        },
    }
    gate.decided_by = operator.operator_id
    gate.decided_at = datetime.now(UTC)
    if gate.gate_kind == "termination_confirmation" and status_value == "approved":
        await AgentSupervisorService(session, operator=operator).terminate(
            run_id=run.id,
            reason="operator_confirmed",
        )
    if status_value == "rejected" and gate.gate_kind in {
        "rollback_conflict",
        "rollback_approval",
        "cross_phase_replan",
    }:
        graph.termination_requested = True
    if gate.gate_kind == "high_risk_approval":
        assert matching_group is not None
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
        raise HTTPException(404, detail=_error("agent_task_not_found", str(error))) from error
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
            "sha256:" + sha256(f"{graph.id}:{graph.cursor}:termination".encode()).hexdigest()
        ),
        status="pending",
    )
    return AgentGraphHumanGateView(
        id=gate.id,
        kind=gate.gate_kind,
        status=gate.status,
        item_count=len(gate.member_ids),
        cursor=gate.cursor,
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
    task = await session.get(ReconciliationTask, task_id)
    report_rollback_eligible = bool(
        task
        and is_fully_successful_sync(
            task,
            report.terminal_state,
            report.facts,
        )
        and await AgentRollbackCycleService(session).blocked_reason(task) is None
    )
    return AgentReportResponse(
        id=report.id,
        task_id=report.task_id,
        kind=report.kind,
        terminal_state=report.terminal_state,
        facts=_sanitize_public(report.facts),
        content=_sanitize_public(report.content),
        rollback_eligible=report_rollback_eligible,
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
    finding_counts = (
        select(
            AgentFindingRecord.task_id.label("task_id"),
            func.count(AgentFindingRecord.id).label("finding_count"),
        )
        .group_by(AgentFindingRecord.task_id)
        .subquery()
    )
    rows = tuple(
        (
            await session.execute(
                select(
                    ReconciliationTask,
                    AgentRunRecord,
                    AgentReportRecord,
                    AgentGraphRunRecord.termination_requested,
                    finding_counts.c.finding_count,
                )
                .join(AgentRunRecord, AgentRunRecord.task_id == ReconciliationTask.id)
                .outerjoin(AgentReportRecord, AgentReportRecord.task_id == ReconciliationTask.id)
                .outerjoin(
                    AgentGraphRunRecord,
                    AgentGraphRunRecord.run_id == AgentRunRecord.id,
                )
                .outerjoin(
                    finding_counts,
                    finding_counts.c.task_id == ReconciliationTask.id,
                )
                .where(
                    ReconciliationTask.tenant_id == operator.tenant_id,
                    ReconciliationTask.workflow_version.in_(("new-agent-v1", "agent-graph-v1")),
                )
                .order_by(ReconciliationTask.created_at.desc(), ReconciliationTask.id.desc())
            )
        ).all()
    )
    items: list[AgentHistoryItem] = []
    rollback_cycles = AgentRollbackCycleService(session)
    for task, run, report, termination_requested, live_finding_count in rows:
        summary = report.facts.get("mutation_summary", {}) if report is not None else {}
        rollback_blocked_reason = await rollback_cycles.blocked_reason(task)
        report_rollback_eligible = bool(
            report
            and is_fully_successful_sync(
                task,
                report.terminal_state,
                report.facts,
            )
        )
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
                termination_requested=bool(termination_requested),
                issue_summary={
                    "total": (
                        len(report.facts.get("findings", []))
                        if report is not None
                        else int(live_finding_count or 0)
                    ),
                    "excluded": (
                        len(report.facts.get("excluded_findings", [])) if report is not None else 0
                    ),
                },
                operation_summary={
                    "succeeded": int(summary.get("succeeded", 0)),
                    "failed": int(summary.get("failed", 0)),
                    "blocked": int(summary.get("blocked", 0)),
                },
                rollback_eligible=bool(
                    report_rollback_eligible
                    and rollback_blocked_reason is None
                ),
                rollback_blocked_reason=rollback_blocked_reason,
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
    source_task = await session.get(ReconciliationTask, task_id)
    if (
        report is None
        or source_task is None
        or source_task.tenant_id != operator.tenant_id
        or not is_fully_successful_sync(
            source_task,
            report.terminal_state,
            report.facts,
        )
    ):
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
    except RollbackAlreadyPerformed as error:
        raise HTTPException(
            409,
            detail=_error(
                "rollback_already_performed",
                "已经回滚，若想再次回滚，需下次同步后执行。",
            ),
        ) from error
    except RollbackCycleChanged as error:
        raise HTTPException(
            409,
            detail=_error(
                "rollback_preview_stale",
                "数据源已完成新一轮同步，请从最新同步任务重新发起回滚。",
            ),
        ) from error
    except ValueError as error:
        raise HTTPException(409, detail=_error("school_lock_conflict", str(error))) from error
    return AgentRollbackPreviewResponse(
        task_id=preview.task_id,
        source_task_id=task_id,
        target_version_id=preview.target_version_id,
        operation_count=len(preview.operations),
        state=preview.state,
        message_zh=preview.message_zh,
        requires_confirmation=preview.requires_confirmation,
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
        await AgentSupervisorService(session, operator=operator).confirm_rollback(task_id=task_id)
        return await _task_response(AgentTaskService(session, operator=operator), task_id)
    except LookupError as error:
        raise HTTPException(404, detail=_error("agent_task_not_found", str(error))) from error
    except RollbackAlreadyPerformed as error:
        raise HTTPException(
            409,
            detail=_error(
                "rollback_already_performed",
                "已经回滚，若想再次回滚，需下次同步后执行。",
            ),
        ) from error
    except RollbackCycleChanged as error:
        raise HTTPException(
            409,
            detail=_error(
                "rollback_preview_stale",
                "数据源已完成新一轮同步，请从最新同步任务重新发起回滚。",
            ),
        ) from error
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


@router.post(
    "/tasks/{task_id}/clarifications/{clarification_id}/selection"
)
async def submit_structured_agent_clarification(
    task_id: UUID,
    clarification_id: UUID,
    body: StructuredClarificationSelectionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> dict[str, object]:
    _require_enabled(request)
    task, run = await AgentTaskService(session, operator=operator).get(task_id)
    if task.workflow_version != "agent-graph-v1":
        raise HTTPException(
            409,
            detail=_error(
                "invalid_state",
                "Structured identity selection requires the Agent graph workflow",
            ),
        )
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
    if graph.cursor != body.graph_cursor:
        raise HTTPException(
            409,
            detail=_error(
                "stale_graph_cursor",
                "Identity conflict candidates changed; refresh before selecting",
            ),
        )
    record = await session.scalar(
        select(AgentClarificationRecord).where(
            AgentClarificationRecord.id == clarification_id,
            AgentClarificationRecord.run_id == run.id,
            AgentClarificationRecord.task_id == task.id,
            AgentClarificationRecord.tenant_id == operator.tenant_id,
        )
    )
    if record is None:
        raise HTTPException(
            404,
            detail=_error("clarification_not_found", "Clarification not found"),
        )
    if record.status not in {"pending", "interpreted"}:
        raise HTTPException(
            409,
            detail=_error(
                "stale_version",
                "Clarification is no longer accepting a selection",
            ),
        )
    gate = await session.scalar(
        select(AgentHumanGateRecord).where(
            AgentHumanGateRecord.graph_run_id == graph.id,
            AgentHumanGateRecord.gate_kind == "identity_conflict",
            AgentHumanGateRecord.status == "pending",
        )
    )
    if (
        gate is None
        or gate.cursor > graph.cursor
        or str(record.id) not in gate.member_ids
    ):
        raise HTTPException(
            409,
            detail=_error(
                "stale_graph_gate",
                "Frozen identity conflict gate is missing or stale",
            ),
        )
    conflict_views = await _graph_identity_conflicts(
        session,
        run_id=run.id,
        clarification_ids=(str(record.id),),
    )
    if len(conflict_views) != 1 or not conflict_views[0].evidence_complete:
        raise HTTPException(
            409,
            detail=_error(
                "incomplete_conflict_evidence",
                "身份冲突证据不完整，不能要求操作人盲目判断。",
            ),
        )

    outcome = (
        "use_candidate"
        if body.decision == "select_candidate"
        else "target_extra"
    )
    if outcome not in record.allowed_outcomes:
        raise HTTPException(
            409,
            detail=_error("invalid_selection", "Selection is outside frozen outcomes"),
        )
    candidate_ids = tuple(
        str(candidate.get("id"))
        for candidate in record.masked_candidates
        if candidate.get("id") is not None
    )
    selected_candidate_id = (
        str(body.selected_candidate_id)
        if body.selected_candidate_id is not None
        else None
    )
    if (
        body.decision == "select_candidate"
        and selected_candidate_id not in candidate_ids
    ):
        raise HTTPException(
            409,
            detail=_error(
                "invalid_selection",
                "Selected candidate is outside frozen candidates",
            ),
        )
    if body.decision == "select_candidate":
        candidate_index = candidate_ids.index(selected_candidate_id)
        candidate_label = (
            chr(ord("A") + candidate_index)
            if candidate_index < 26
            else str(candidate_index + 1)
        )
        interpretation_zh = (
            f"你选择了第三方候选 {candidate_label}，确认后继续。"
        )
    else:
        interpretation_zh = "你选择了按希沃多余处理，确认后继续。"
    try:
        updated, created_or_replaced = await AgentGovernanceRepository(
            session
        ).record_structured_clarification_selection(
            record.id,
            tenant_id=operator.tenant_id,
            decision=body.decision,
            selected_candidate_id=body.selected_candidate_id,
            note=body.note,
            interpretation_zh=interpretation_zh,
            idempotency_key=body.idempotency_key,
            actor_id=operator.operator_id,
        )
    except GovernanceReplayConflict as error:
        raise HTTPException(
            409,
            detail=_error("invalid_selection", str(error)),
        ) from error
    if created_or_replaced:
        await AgentRuntimeRepository(session).append_event(
            run.id,
            "clarification_decision_ready",
            {
                "decision_id": str(updated.id),
                "outcome": outcome,
                "candidate_id": selected_candidate_id,
                "interpretation_zh": interpretation_zh,
                "submission_source": "structured_selection",
            },
        )
    return {
        "decision_id": str(updated.id),
        "status": updated.status,
        "task_id": str(task.id),
        "decision": body.decision,
        "selected_candidate_id": selected_candidate_id,
        "interpretation_zh": interpretation_zh,
        "requires_second_confirmation": True,
    }


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
            AgentClarificationRecord.status.in_(("pending", "interpreted")),
        )
        .order_by(
            case((AgentClarificationRecord.status == "interpreted", 0), else_=1),
            AgentClarificationRecord.created_at,
            AgentClarificationRecord.id,
        )
    )
    if record is None:
        raise HTTPException(
            409,
            detail=_error("clarification_required", "No pending clarification"),
        )
    try:
        candidate_ids = tuple(
            UUID(str(item["id"])) for item in record.masked_candidates if "id" in item
        )
    except (TypeError, ValueError):
        candidate_ids = ()
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
        conflict_views = await _graph_identity_conflicts(
            session,
            run_id=run.id,
            clarification_ids=(str(record.id),),
        )
        if (
            gate.cursor > graph.cursor
            or len(conflict_views) != 1
            or not conflict_views[0].evidence_complete
            or len(candidate_ids) != len(record.masked_candidates)
        ):
            raise HTTPException(
                409,
                detail=_error(
                    "incomplete_conflict_evidence",
                    "身份冲突证据不完整，不能要求操作人盲目判断。",
                ),
            )
        normalized_message = body.message.strip()
        if record.original_text == normalized_message and record.interpretation:
            outcome = str(record.interpretation.get("outcome", ""))
            decision_name = (
                "select_candidate"
                if outcome == "use_candidate"
                else "treat_as_extra"
                if outcome == "target_extra"
                else "leave_unresolved"
            )
            return {
                "decision_id": str(record.id),
                "status": record.status,
                "task_id": str(task.id),
                "decision": decision_name,
                "selected_candidate_id": record.interpretation.get("candidate_id"),
                "interpretation_zh": record.interpretation.get(
                    "interpretation_zh",
                    "模型已形成受限解释，请确认后继续。",
                ),
                "requires_second_confirmation": (
                    record.status == "interpreted"
                    and decision_name != "leave_unresolved"
                ),
            }
        resource_id = f"identity-conflict:{record.id}"
        instruction_hash = sha256(normalized_message.encode("utf-8")).hexdigest()[:12]
        action_id = f"interpret_identity_conflict:{record.id}:{instruction_hash}"
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
                masked_candidates=tuple(
                    _sanitized_frozen_candidate(item)
                    for item in record.masked_candidates
                ),
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
            revision_failure_categories = {
                "model_provider_failure",
                "model_output_failure",
                "model_contract_failure",
                "tool_argument_rejected",
            }
            if error.failure_categories and set(error.failure_categories).issubset(
                revision_failure_categories
            ):
                feedback_zh = (
                    "模型未能安全理解这条说明。请补充更明确的处理意见："
                    "选择一个第三方候选，或明确按“希沃多余”处理。"
                )
                updated = await AgentGovernanceRepository(
                    session
                ).record_clarification_feedback(
                    record.id,
                    original_text=body.message,
                    feedback_zh=feedback_zh,
                    actor_id=operator.operator_id,
                )
                await AgentRuntimeRepository(session).append_event(
                    run.id,
                    "clarification_revision_required",
                    {
                        "decision_id": str(updated.id),
                        "interpretation_zh": feedback_zh,
                        "reason": "model_interpretation_failure",
                        "failure_categories": list(error.failure_categories),
                        "attempt_count": error.attempt_count,
                    },
                )
                return {
                    "decision_id": str(updated.id),
                    "status": updated.status,
                    "task_id": str(task.id),
                    "decision": "leave_unresolved",
                    "selected_candidate_id": None,
                    "interpretation_zh": feedback_zh,
                    "requires_second_confirmation": False,
                }
            raise HTTPException(
                503,
                detail=_error(
                    "agent_model_failure",
                    "AI 无法解释当前说明，请重试或终止任务。",
                ),
            ) from error
        if draft.decision == "leave_unresolved":
            updated = await AgentGovernanceRepository(
                session
            ).record_clarification_feedback(
                record.id,
                original_text=body.message,
                feedback_zh=draft.interpretation_zh,
                actor_id=operator.operator_id,
            )
            await AgentRuntimeRepository(session).append_event(
                run.id,
                "clarification_revision_required",
                {
                    "decision_id": str(updated.id),
                    "interpretation_zh": draft.interpretation_zh,
                },
            )
            return {
                "decision_id": str(updated.id),
                "status": updated.status,
                "task_id": str(task.id),
                "decision": draft.decision,
                "selected_candidate_id": None,
                "interpretation_zh": draft.interpretation_zh,
                "requires_second_confirmation": False,
            }
        outcome = "use_candidate" if draft.decision == "select_candidate" else "target_extra"
        updated = await AgentGovernanceRepository(session).record_clarification_interpretation(
            record.id,
            original_text=body.message,
            interpretation={
                "outcome": outcome,
                "candidate_id": (
                    str(draft.selected_candidate_id) if draft.selected_candidate_id else None
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
                    str(draft.selected_candidate_id) if draft.selected_candidate_id else None
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
                str(draft.selected_candidate_id) if draft.selected_candidate_id else None
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
    await _resume_waiting_run(session, run_id, expected_phase="aggregate_risk_and_approvals")


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
    await _resume_waiting_run(session, run_id, expected_phase="clarify_identity_conflicts")


async def _resume_waiting_run(session: AsyncSession, run_id: UUID, *, expected_phase: str) -> None:
    repository = AgentRuntimeRepository(session)
    run = await repository.get_run(run_id, for_update=True)
    if run is None or run.phase != expected_phase or run.status != "waiting_human":
        return
    resumed = await repository.transition_run(run.id, requested_status=AgentRunStatus.RUNNING)
    await repository.append_event(
        run.id,
        "run.resumed",
        {"phase": resumed.phase, "status": resumed.status},
    )


_PHONE_PATTERN = re.compile(r"(?<!\d)1\d{10}(?!\d)")
_EMAIL_PATTERN = re.compile(
    r"\b([A-Za-z0-9._%+-])([A-Za-z0-9._%+-]*)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


def _intent_view(
    context: Mapping[str, Any],
    *,
    remote_origins: Mapping[str, str],
) -> AgentIntentView:
    payload = dict(context)
    source = payload.get("source")
    if isinstance(source, dict) and source.get("kind") == "remote_csv":
        source_view = dict(source)
        remote_source_id = source_view.get("remote_source_id")
        display_origin = remote_origins.get(str(remote_source_id))
        if display_origin is not None:
            source_view["display_origin"] = display_origin
        payload["source"] = source_view
    return AgentIntentView.model_validate(payload)


def _sanitize_public(value: Any, *, field: str | None = None) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_public(item, field=str(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_public(item, field=field) for item in value]
    if isinstance(value, str):
        if field in {"phone", "student_phone", "手机号", "电话"}:
            return f"***{value[-4:]}" if value else value
        sanitized = _PHONE_PATTERN.sub(
            lambda match: f"***{match.group(0)[-4:]}",
            value,
        )
        return _EMAIL_PATTERN.sub(
            lambda match: f"{match.group(1)}***@{match.group(3)}",
            sanitized,
        )
    return value

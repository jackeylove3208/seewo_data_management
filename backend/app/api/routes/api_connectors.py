from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.external_identity_service import (
    AgentExternalIdentityService,
    ExternalIdentityBindingConflict,
    ExternalIdentityBindingNotFound,
    ExternalIdentityBindingValidation,
)
from app.api.dependencies import get_operator_context, get_session
from app.api_connectors.dingtalk_configuration import entity_kinds_for_scope
from app.api_connectors.policy import uses_task_scoped_conversation_credentials
from app.api_connectors.service import (
    ApiConnectionConflictError,
    ApiConnectionNotFoundError,
    ApiConnectionService,
    ApiConnectionValidationError,
)
from app.core.security import OperatorContext
from app.models.agent_runtime import AgentConversationRecord
from app.models.api_connectors import ApiConfigurationSessionRecord
from app.schemas.api_connectors import (
    ApiConfigurationSessionCreate,
    ApiConfigurationSessionRead,
    ApiConnectionCreate,
    ApiConnectionRead,
    ApiConnectionRotateSecret,
    ApiConnectionTestRequest,
    ApiProviderRead,
    ExternalIdentityBindingConfirm,
    ExternalIdentityBindingRead,
)

router = APIRouter(prefix="/api/connectors", tags=["api-connectors"])
external_identity_router = APIRouter(
    prefix="/api/agent/external-identity-bindings",
    tags=["agent-external-identity"],
)
_CONFIGURATION_SESSION_TTL = timedelta(minutes=10)


@router.get("/providers", response_model=list[ApiProviderRead])
async def list_api_providers(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ApiProviderRead]:
    service = _service(request, session)
    return [
        ApiProviderRead(
            provider_id=manifest.provider_id,
            manifest_version=manifest.manifest_version,
            adapter_version=manifest.adapter_version,
            supported_entities=tuple(sorted(manifest.supported_entities)),
            required_secret_fields=manifest.required_secret_fields,
            required_capabilities=manifest.required_capabilities,
            projection_version=manifest.projection_version,
        )
        for manifest in service.providers()
    ]


@router.post(
    "/configuration-sessions",
    response_model=ApiConfigurationSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_configuration_session(
    payload: ApiConfigurationSessionCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ApiConfigurationSessionRead:
    service = _service(request, session)
    try:
        manifest = next(
            item for item in service.providers() if item.provider_id == payload.provider_id
        )
    except StopIteration as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="API provider is not registered",
        ) from error
    session_id = uuid4()
    expires_at = datetime.now(UTC) + _CONFIGURATION_SESSION_TTL
    connection_scope = "persistent"
    if payload.conversation_id is not None:
        conversation = await session.scalar(
            select(AgentConversationRecord).where(
                AgentConversationRecord.id == payload.conversation_id,
                AgentConversationRecord.tenant_id == operator.tenant_id,
                AgentConversationRecord.created_by == operator.operator_id,
                AgentConversationRecord.status == "active",
            )
        )
        if conversation is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail="Active conversation not found",
            )
        if uses_task_scoped_conversation_credentials(payload.provider_id):
            connection_scope = "task_ephemeral"
    session.add(
        ApiConfigurationSessionRecord(
            id=session_id,
            tenant_id=operator.tenant_id,
            provider_id=payload.provider_id,
            conversation_id=(
                payload.conversation_id
                if connection_scope == "task_ephemeral"
                else None
            ),
            created_by=operator.operator_id,
            connection_scope=connection_scope,
            expires_at=expires_at,
            consumed_at=None,
        )
    )
    await session.flush()
    return ApiConfigurationSessionRead(
        id=session_id,
        provider_id=payload.provider_id,
        required_secret_fields=manifest.required_secret_fields,
        expires_at=expires_at,
    )


@router.post(
    "/connections",
    response_model=ApiConnectionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_connection(
    payload: ApiConnectionCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ApiConnectionRead:
    configuration_session = await _consume_configuration_session(
        session,
        operator,
        payload,
    )
    service = _service(request, session)
    try:
        connection = await service.create(
            tenant_id=operator.tenant_id,
            operator_id=operator.operator_id,
            provider_id=payload.provider_id,
            display_name=payload.display_name,
            public_configuration=payload.public_configuration,
            secret=payload.secret,
            scope=configuration_session.connection_scope,
            conversation_id=configuration_session.conversation_id,
        )
    except (ApiConnectionValidationError, ValueError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    except ApiConnectionConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
    if configuration_session.conversation_id is not None:
        conversation = await session.scalar(
            select(AgentConversationRecord)
            .where(
                AgentConversationRecord.id == configuration_session.conversation_id,
                AgentConversationRecord.tenant_id == operator.tenant_id,
                AgentConversationRecord.status == "active",
            )
            .with_for_update()
        )
        entity_types = _connection_entity_types(
            payload.provider_id,
            payload.public_configuration,
        )
        if conversation is not None and entity_types:
            default_target = request.app.state.settings.database_connector_configurations.get(
                "seewo-data-mysql"
            )
            conversation.context = {
                **conversation.context,
                "title": conversation.context.get("title")
                or _connection_title(payload.provider_id, payload.public_configuration),
                "entity_types": entity_types,
                "source": {
                    "kind": "api",
                    "configuration_id": str(connection.id),
                },
                **(
                    {
                        "target": {
                            "kind": "database",
                            "configuration_id": "seewo-data-mysql",
                        }
                    }
                    if default_target is not None
                    and default_target.source_role == "target"
                    and default_target.dialect == "mysql"
                    and conversation.context.get("target") is None
                    else {}
                ),
            }
    return ApiConnectionRead.model_validate(connection, from_attributes=True)


@router.get("/connections", response_model=list[ApiConnectionRead])
async def list_api_connections(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> list[ApiConnectionRead]:
    return [
        ApiConnectionRead.model_validate(item, from_attributes=True)
        for item in await _service(request, session).list(tenant_id=operator.tenant_id)
    ]


@router.get("/connections/{connection_id}", response_model=ApiConnectionRead)
async def get_api_connection(
    connection_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ApiConnectionRead:
    try:
        item = await _service(request, session).get(
            tenant_id=operator.tenant_id,
            connection_id=connection_id,
        )
    except ApiConnectionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return ApiConnectionRead.model_validate(item, from_attributes=True)


@router.post("/connections/{connection_id}/test", response_model=ApiConnectionRead)
async def test_api_connection(
    connection_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    payload: ApiConnectionTestRequest | None = None,
) -> ApiConnectionRead:
    conversation_id = payload.conversation_id if payload is not None else None
    conversation: AgentConversationRecord | None = None
    if conversation_id is not None:
        conversation = await session.scalar(
            select(AgentConversationRecord)
            .where(
                AgentConversationRecord.id == conversation_id,
                AgentConversationRecord.tenant_id == operator.tenant_id,
                AgentConversationRecord.created_by == operator.operator_id,
                AgentConversationRecord.status == "active",
            )
            .with_for_update()
        )
        if conversation is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail="Active conversation not found",
            )
    try:
        item = await _service(request, session).test(
            tenant_id=operator.tenant_id,
            operator_id=operator.operator_id,
            connection_id=connection_id,
            conversation_id=conversation_id,
        )
    except ApiConnectionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ApiConnectionConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
    if conversation is not None:
        source = conversation.context.get("source")
        source_matches = (
            isinstance(source, dict)
            and source.get("kind") == "api"
            and source.get("configuration_id") == str(connection_id)
        )
        entity_types = _connection_entity_types(
            item.provider_id,
            item.public_configuration,
        )
        can_confirm = (
            item.state == "active"
            and source_matches
            and bool(entity_types)
            and item.visibility_summary.get("visible") is True
            and all(
                item.capabilities.get(f"entity.{entity_kind}.read") is True
                and _is_positive_count(
                    item.visibility_summary.get(f"{entity_kind}_count")
                )
                for entity_kind in entity_types
            )
            and conversation.context.get("target") is not None
        )
        if source_matches:
            previous_entity_types = conversation.context.get("entity_types")
            entity_changed = previous_entity_types != entity_types
            conversation.context = {
                **conversation.context,
                **(
                    {
                        "title": _connection_title(
                            item.provider_id,
                            item.public_configuration,
                            fallback_label=item.display_name,
                        ),
                        "entity_types": entity_types,
                    }
                    if entity_types and entity_changed
                    else {}
                ),
                "decision_kind": (
                    "start_confirmation" if can_confirm else "api_configuration"
                ),
            }
    return ApiConnectionRead.model_validate(item, from_attributes=True)


@router.post(
    "/connections/{connection_id}/rotate-secret",
    response_model=ApiConnectionRead,
)
async def rotate_api_connection_secret(
    connection_id: UUID,
    payload: ApiConnectionRotateSecret,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ApiConnectionRead:
    try:
        item = await _service(request, session).rotate(
            tenant_id=operator.tenant_id,
            operator_id=operator.operator_id,
            connection_id=connection_id,
            secret=payload.secret,
            public_configuration=payload.public_configuration,
            conversation_id=payload.conversation_id,
        )
    except ApiConnectionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ApiConnectionValidationError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    except ApiConnectionConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ApiConnectionRead.model_validate(item, from_attributes=True)


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_api_connection(
    connection_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> Response:
    try:
        await _service(request, session).delete(
            tenant_id=operator.tenant_id,
            connection_id=connection_id,
        )
    except ApiConnectionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ApiConnectionConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _service(request: Request, session: AsyncSession) -> ApiConnectionService:
    settings = request.app.state.settings
    if not settings.new_agent_api_connector_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="API connectors are disabled")
    if settings.api_connector_secret_key is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API connector secret storage is unavailable",
        )
    return ApiConnectionService(
        session,
        registry=request.app.state.api_provider_registry,
        fernet_key=settings.api_connector_secret_key,
    )


def _connection_entity_types(
    provider_id: str,
    public_configuration: Mapping[str, object],
) -> list[str]:
    if provider_id == "dingtalk":
        try:
            return [
                kind.value
                for kind in entity_kinds_for_scope(
                    public_configuration,
                    allow_legacy=True,
                )
            ]
        except ApiConnectionValidationError:
            return []
    configured_entity = public_configuration.get("person_entity_kind")
    return (
        [configured_entity]
        if isinstance(configured_entity, str)
        and configured_entity in {"student", "teacher"}
        else []
    )


def _connection_title(
    provider_id: str,
    public_configuration: Mapping[str, object],
    *,
    fallback_label: str | None = None,
) -> str:
    if provider_id == "dingtalk":
        scope = public_configuration.get("sync_scope")
        scope_labels = {
            "department": "部门",
            "people": "人员",
            "all": "全部",
        }
        scope_label = scope_labels.get(scope) if isinstance(scope, str) else None
        if scope_label is not None:
            return f"钉钉{scope_label}同步"
    entity_types = _connection_entity_types(provider_id, public_configuration)
    entity_label = {"student": "学生", "teacher": "教师"}.get(
        entity_types[0] if len(entity_types) == 1 else ""
    )
    return f"{fallback_label or provider_id}{entity_label or '组织'}同步"


def _is_positive_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


async def _consume_configuration_session(
    session: AsyncSession,
    operator: OperatorContext,
    payload: ApiConnectionCreate,
) -> ApiConfigurationSessionRecord:
    configuration_session = await session.scalar(
        select(ApiConfigurationSessionRecord)
        .where(
            ApiConfigurationSessionRecord.id == payload.configuration_session_id,
            ApiConfigurationSessionRecord.tenant_id == operator.tenant_id,
            ApiConfigurationSessionRecord.provider_id == payload.provider_id,
            ApiConfigurationSessionRecord.created_by == operator.operator_id,
        )
        .with_for_update()
    )
    expires_at = (
        configuration_session.expires_at
        if configuration_session is not None
        else None
    )
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        configuration_session is None
        or configuration_session.consumed_at is not None
        or expires_at is None
        or expires_at <= datetime.now(UTC)
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="API configuration session is invalid or expired",
        )
    configuration_session.consumed_at = datetime.now(UTC)
    await session.flush()
    return configuration_session


@external_identity_router.get(
    "",
    response_model=list[ExternalIdentityBindingRead],
)
async def list_external_identity_bindings(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> list[ExternalIdentityBindingRead]:
    _require_external_identity_enabled(request)
    return [
        ExternalIdentityBindingRead.model_validate(item)
        for item in await AgentExternalIdentityService(session).list(
            tenant_id=operator.tenant_id
        )
    ]


@external_identity_router.post(
    "",
    response_model=ExternalIdentityBindingRead,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_external_identity_binding(
    payload: ExternalIdentityBindingConfirm,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ExternalIdentityBindingRead:
    _require_external_identity_enabled(request)
    try:
        binding = await AgentExternalIdentityService(session).confirm(
            tenant_id=operator.tenant_id,
            operator_id=operator.operator_id,
            run_id=payload.run_id,
            connection_id=payload.connection_id,
            entity_kind=payload.entity_kind.value,
            authority_stable_locator=payload.authority_stable_locator,
            target_connector_id=payload.target_connector_id,
            target_stable_locator=payload.target_stable_locator,
        )
    except ExternalIdentityBindingValidation as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except ExternalIdentityBindingConflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ExternalIdentityBindingRead.model_validate(binding)


@external_identity_router.post(
    "/{binding_id}/revoke",
    response_model=ExternalIdentityBindingRead,
)
async def revoke_external_identity_binding(
    binding_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ExternalIdentityBindingRead:
    _require_external_identity_enabled(request)
    try:
        binding = await AgentExternalIdentityService(session).revoke(
            tenant_id=operator.tenant_id,
            operator_id=operator.operator_id,
            binding_id=binding_id,
        )
    except ExternalIdentityBindingNotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return ExternalIdentityBindingRead.model_validate(binding)


def _require_external_identity_enabled(request: Request) -> None:
    if not request.app.state.settings.new_agent_api_connector_enabled:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="API connectors are disabled",
        )

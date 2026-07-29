from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.external_identity_service import (
    AgentExternalIdentityService,
    ExternalIdentityBindingConflict,
    ExternalIdentityBindingNotFound,
    ExternalIdentityBindingValidation,
)
from app.api.dependencies import get_operator_context, get_session
from app.api_connectors.service import (
    ApiConnectionConflictError,
    ApiConnectionNotFoundError,
    ApiConnectionService,
    ApiConnectionValidationError,
)
from app.core.security import OperatorContext
from app.schemas.api_connectors import (
    ApiConfigurationSessionCreate,
    ApiConfigurationSessionRead,
    ApiConnectionCreate,
    ApiConnectionRead,
    ApiConnectionRotateSecret,
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
    request.app.state.api_configuration_sessions[session_id] = {
        "tenant_id": operator.tenant_id,
        "provider_id": payload.provider_id,
        "expires_at": expires_at,
    }
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
    _consume_configuration_session(request, operator, payload)
    service = _service(request, session)
    try:
        connection = await service.create(
            tenant_id=operator.tenant_id,
            operator_id=operator.operator_id,
            provider_id=payload.provider_id,
            display_name=payload.display_name,
            public_configuration=payload.public_configuration,
            secret=payload.secret,
        )
    except (ApiConnectionValidationError, ValueError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    except ApiConnectionConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
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
) -> ApiConnectionRead:
    try:
        item = await _service(request, session).test(
            tenant_id=operator.tenant_id,
            operator_id=operator.operator_id,
            connection_id=connection_id,
        )
    except ApiConnectionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ApiConnectionConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
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


def _consume_configuration_session(
    request: Request,
    operator: OperatorContext,
    payload: ApiConnectionCreate,
) -> None:
    session_data: dict[str, Any] | None = request.app.state.api_configuration_sessions.pop(
        payload.configuration_session_id,
        None,
    )
    if (
        session_data is None
        or session_data["tenant_id"] != operator.tenant_id
        or session_data["provider_id"] != payload.provider_id
        or session_data["expires_at"] <= datetime.now(UTC)
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="API configuration session is invalid or expired",
        )


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

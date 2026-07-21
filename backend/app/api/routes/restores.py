from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.llm import HttpLLMProvider
from app.api.dependencies import get_operator_context, get_session
from app.api.routes.execution_batches import _executor
from app.core.security import OperatorContext
from app.restores.service import RestoreService
from app.schemas.executions import ExecutionBatchResult
from app.schemas.reporting import (
    ConfirmRestoreRequest,
    RestoreConfirmation,
    RestorePreview,
    TargetVersionView,
)

router = APIRouter(prefix="/api", tags=["historical-restores"])


def _service(request: Request, session: AsyncSession, operator: OperatorContext) -> RestoreService:
    settings = request.app.state.settings
    secret = (
        settings.tokenization_secret.get_secret_value()
        if settings.tokenization_secret is not None
        else None
    )
    return RestoreService(
        session,
        operator=operator,
        provider=HttpLLMProvider(settings=settings),
        tokenization_secret=secret,
    )


@router.get(
    "/reconciliation-tasks/{task_id}/target-versions",
    response_model=list[TargetVersionView],
)
async def list_target_versions(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> list[TargetVersionView]:
    try:
        return [
            TargetVersionView.model_validate(item)
            for item in await _service(request, session, operator).versions(task_id)
        ]
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/target-versions/{target_version_id}/restore-preview", response_model=RestorePreview)
async def preview_restore(
    target_version_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> RestorePreview:
    try:
        return await _service(request, session, operator).preview(target_version_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/restores", response_model=RestoreConfirmation, status_code=status.HTTP_202_ACCEPTED)
async def confirm_restore(
    body: ConfirmRestoreRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> RestoreConfirmation:
    try:
        return await _service(request, session, operator).confirm(
            body.preview_hash,
            idempotency_key=idempotency_key,
            high_risk_acknowledged=body.high_risk_acknowledged,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/restores/{restore_request_id}/execute",
    response_model=ExecutionBatchResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute_restore(
    restore_request_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ExecutionBatchResult:
    try:
        return await _service(request, session, operator).execute(
            restore_request_id,
            executor=_executor(request, session),
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

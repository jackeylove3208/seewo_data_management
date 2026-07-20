import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import ModelProviderError
from app.ai.providers.llm import HttpLLMProvider
from app.api.dependencies import get_operator_context, get_session
from app.core.security import OperatorContext
from app.executions.csv_versioning import CsvTargetVersioner
from app.executions.executor import ExecutionExecutor
from app.executions.record_service import ExecutionRecordService
from app.executions.service import ExecutionPlanningConflict, ExecutionPlanningService
from app.executions.verifier import TargetVerifier
from app.governance.plan_explainer import GovernancePlanExplainer
from app.repositories.executions import ExecutionRepository
from app.schemas.executions import (
    ConfirmExecutionBatchRequest,
    ExecutionBatchConfirmation,
    ExecutionBatchResult,
    ExecutionPreview,
    ExecutionPreviewRequest,
    OperationStatus,
    PlanExplanationResponse,
    RetryExecutionRequest,
)

router = APIRouter(prefix="/api", tags=["execution-batches"])


@router.post("/execution-batches/preview", response_model=ExecutionPreview)
async def preview_execution_batch(
    body: ExecutionPreviewRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ExecutionPreview:
    try:
        return await ExecutionPlanningService(session, operator=operator).preview(body)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ExecutionPlanningConflict, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/execution-batches",
    response_model=ExecutionBatchConfirmation,
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirm_execution_batch(
    body: ConfirmExecutionBatchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> ExecutionBatchConfirmation:
    try:
        return await ExecutionPlanningService(session, operator=operator).confirm(
            body,
            idempotency_key=idempotency_key,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ExecutionPlanningConflict as error:
        conflict_detail: object = str(error)
        try:
            conflict_detail = json.loads(str(error))
        except json.JSONDecodeError:
            pass
        raise HTTPException(status_code=409, detail=conflict_detail) from error


@router.post(
    "/governance-plans/{plan_id}/explanation",
    response_model=PlanExplanationResponse,
    responses={503: {"description": "Optional explanation is unavailable"}},
)
async def explain_governance_plan(
    plan_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> PlanExplanationResponse | JSONResponse:
    try:
        parsed_plan_id = UUID(plan_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid plan id") from error
    service = ExecutionPlanningService(session, operator=operator)
    try:
        plan = await service.get_plan(parsed_plan_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    settings = request.app.state.settings
    tokenization_secret = (
        settings.tokenization_secret.get_secret_value()
        if settings.tokenization_secret is not None
        else None
    )
    explainer = GovernancePlanExplainer(
        HttpLLMProvider(settings=settings),
        tokenization_secret=tokenization_secret,
    )
    try:
        response = await explainer.explain(plan, tenant_id=operator.tenant_id)
        await ExecutionRepository(session).append_plan_explanation(plan.id, response)
        return response
    except (ModelProviderError, ValueError) as error:
        return JSONResponse(
            status_code=503,
            content={"state": "unavailable", "reason": str(error)},
        )


def _executor(request: Request, session: AsyncSession) -> ExecutionExecutor:
    repository = ExecutionRepository(session)
    return ExecutionExecutor(
        repository=repository,
        target=CsvTargetVersioner(
            repository=repository,
            output_root=request.app.state.settings.export_root,
        ),
        verifier=TargetVerifier(),
    )


@router.post(
    "/execution-batches/{batch_id}/execute",
    response_model=ExecutionBatchResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute_batch(
    batch_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ExecutionBatchResult:
    try:
        detail = await ExecutionRecordService(session, operator=operator).get_detail(batch_id)
        await ExecutionPlanningService(session, operator=operator).revalidate(detail.plan_id)
        return await _executor(request, session).execute(batch_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, OSError) as error:
        conflict_detail: object = str(error)
        try:
            conflict_detail = json.loads(str(error))
        except json.JSONDecodeError:
            pass
        raise HTTPException(status_code=409, detail=conflict_detail) from error


@router.post(
    "/execution-batches/{batch_id}/retry",
    response_model=ExecutionBatchResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_batch(
    batch_id: UUID,
    body: RetryExecutionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ExecutionBatchResult:
    try:
        detail = await ExecutionRecordService(session, operator=operator).get_detail(batch_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    retryable = {
        operation.record_id
        for operation in detail.operations
        if operation.attempts
        and operation.attempts[-1].status is OperationStatus.FAILED
        and operation.attempts[-1].retryable
    }
    requested = frozenset(body.operation_ids)
    if not requested <= retryable:
        raise HTTPException(status_code=409, detail="operation is not eligible for retry")
    try:
        return await _executor(request, session).execute(
            batch_id,
            retry_only=requested,
        )
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

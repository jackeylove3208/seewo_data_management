from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.job_service import AnalysisJobService
from app.ai.providers.embeddings import HttpEmbeddingProvider
from app.api.dependencies import get_operator_context, get_session
from app.core.security import OperatorContext
from app.differences.service import DifferenceDetectionService
from app.ingestion.field_mapping import default_mapping_registry
from app.ingestion.service import IngestionServiceError, ReconciliationIngestionService
from app.matching.service import EntityResolutionService
from app.matching.vector_index import VectorIndex
from app.repositories.tasks import TaskRepository
from app.schemas.api_ingestion import (
    CreateReconciliationTaskRequest,
    ReconciliationTaskResponse,
)
from app.schemas.canonical_entities import SourceRole
from app.schemas.matching import ResolutionSummary
from app.schemas.workflow import WorkflowAdvanceResponse
from app.workflow.service import ReconciliationWorkflowService

router = APIRouter(prefix="/api", tags=["reconciliation-tasks"])


def service_for(
    request: Request,
    session: AsyncSession,
) -> ReconciliationIngestionService:
    return ReconciliationIngestionService(
        session,
        request.app.state.settings,
        default_mapping_registry(),
    )


def workflow_service_for(
    request: Request,
    session: AsyncSession,
    operator: OperatorContext,
) -> ReconciliationWorkflowService:
    settings = request.app.state.settings
    vector_index = None
    tokenization_secret = None
    if settings.rematching_enabled and settings.embedding_url and settings.embedding_api_key:
        vector_index = VectorIndex(session, HttpEmbeddingProvider(settings=settings))
        tokenization_secret = (
            settings.tokenization_secret.get_secret_value()
            if settings.tokenization_secret is not None
            else None
        )
    return ReconciliationWorkflowService(
        session,
        operator=operator,
        resolver=EntityResolutionService(
            session,
            vector_index=vector_index,
            tokenization_secret=tokenization_secret,
            rematching_top_k=settings.rematching_top_k,
        ),
        detector=DifferenceDetectionService(session),
        analyzer=AnalysisJobService(session, operator=operator),
        rematching_enabled=settings.rematching_enabled,
    )


@router.post(
    "/reconciliation-tasks",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReconciliationTaskResponse,
)
async def create_reconciliation_task(
    body: CreateReconciliationTaskRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> ReconciliationTaskResponse:
    try:
        return await service_for(request, session).create_task(
            body,
            idempotency_key,
            operator.tenant_id,
        )
    except IngestionServiceError as error:
        raise HTTPException(error.status_code, detail=error.as_detail()) from error


@router.get(
    "/reconciliation-tasks/{task_id}",
    response_model=ReconciliationTaskResponse,
)
async def get_reconciliation_task(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ReconciliationTaskResponse:
    try:
        return await service_for(request, session).get_task(task_id, operator.tenant_id)
    except IngestionServiceError as error:
        raise HTTPException(error.status_code, detail=error.as_detail()) from error


@router.post(
    "/reconciliation-tasks/{task_id}/resolve",
    response_model=ResolutionSummary,
)
async def resolve_reconciliation_task(
    task_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ResolutionSummary:
    await _require_task(session, task_id, operator)
    try:
        return await EntityResolutionService(session).resolve_task(task_id)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(409, detail=str(error)) from error


@router.post(
    "/reconciliation-tasks/{task_id}/workflow/advance",
    response_model=WorkflowAdvanceResponse,
)
async def advance_reconciliation_workflow(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> WorkflowAdvanceResponse:
    try:
        return await workflow_service_for(request, session, operator).advance(task_id)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(409, detail=str(error)) from error


@router.post(
    "/reconciliation-tasks/{task_id}/workflow/retry",
    response_model=WorkflowAdvanceResponse,
)
async def retry_reconciliation_workflow(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> WorkflowAdvanceResponse:
    try:
        return await workflow_service_for(request, session, operator).retry(task_id)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(409, detail=str(error)) from error


@router.get("/reconciliation-tasks/{task_id}/quarantine/{source_role}")
async def download_quarantine(
    task_id: UUID,
    source_role: SourceRole,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> FileResponse:
    try:
        path = await service_for(request, session).quarantine_path(
            task_id,
            source_role,
            operator.tenant_id,
        )
    except IngestionServiceError as error:
        raise HTTPException(error.status_code, detail=error.as_detail()) from error
    return FileResponse(
        path,
        media_type="text/csv",
        filename=f"quarantine-{source_role.value}.csv",
    )


async def _require_task(
    session: AsyncSession,
    task_id: UUID,
    operator: OperatorContext,
) -> None:
    task = await TaskRepository(session).get(task_id)
    if task is None or task.tenant_id != operator.tenant_id:
        raise HTTPException(404, detail=f"reconciliation task not found: {task_id}")

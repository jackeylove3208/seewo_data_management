from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.ingestion.field_mapping import default_mapping_registry
from app.ingestion.service import IngestionServiceError, ReconciliationIngestionService
from app.matching.service import EntityResolutionService
from app.schemas.api_ingestion import (
    CreateReconciliationTaskRequest,
    ReconciliationTaskResponse,
)
from app.schemas.canonical_entities import SourceRole
from app.schemas.matching import ResolutionSummary

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


@router.post(
    "/reconciliation-tasks",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReconciliationTaskResponse,
)
async def create_reconciliation_task(
    body: CreateReconciliationTaskRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> ReconciliationTaskResponse:
    try:
        return await service_for(request, session).create_task(body, idempotency_key)
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
) -> ReconciliationTaskResponse:
    try:
        return await service_for(request, session).get_task(task_id)
    except IngestionServiceError as error:
        raise HTTPException(error.status_code, detail=error.as_detail()) from error


@router.post(
    "/reconciliation-tasks/{task_id}/resolve",
    response_model=ResolutionSummary,
)
async def resolve_reconciliation_task(
    task_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ResolutionSummary:
    try:
        return await EntityResolutionService(session).resolve_task(task_id)
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
) -> FileResponse:
    try:
        path = await service_for(request, session).quarantine_path(task_id, source_role)
    except IngestionServiceError as error:
        raise HTTPException(error.status_code, detail=error.as_detail()) from error
    return FileResponse(
        path,
        media_type="text/csv",
        filename=f"quarantine-{source_role.value}.csv",
    )

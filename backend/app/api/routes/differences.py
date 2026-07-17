from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_operator_context, get_session
from app.core.security import OperatorContext
from app.differences.service import DifferenceDetectionService
from app.repositories.differences import DifferenceRepository
from app.repositories.tasks import TaskRepository
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import (
    DifferenceFilters,
    DifferenceItem,
    DifferencePage,
    DifferenceStatus,
    DifferenceSummary,
    DifferenceType,
)

router = APIRouter(prefix="/api", tags=["differences"])


@router.post(
    "/reconciliation-tasks/{task_id}/differences/detect",
    response_model=DifferenceSummary,
)
async def detect_differences(
    task_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> DifferenceSummary:
    await _require_task(session, task_id, operator)
    try:
        return await DifferenceDetectionService(session).detect(task_id)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(409, detail=str(error)) from error


@router.get(
    "/reconciliation-tasks/{task_id}/differences",
    response_model=DifferencePage,
)
async def list_differences(
    task_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    entity_type: EntityType | None = None,
    difference_type: DifferenceType | None = None,
    analysis_status: Annotated[str | None, Query(max_length=32)] = None,
    risk: Annotated[str | None, Query(max_length=32)] = None,
    resolution_status: DifferenceStatus | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DifferencePage:
    await _require_task(session, task_id, operator)
    filters = DifferenceFilters(
        entity_type=entity_type,
        difference_type=difference_type,
        analysis_status=analysis_status,
        risk=risk,
        resolution_status=resolution_status,
        cursor=cursor,
        limit=limit,
    )
    try:
        return await DifferenceRepository(session).list_page(task_id, filters)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error


@router.get("/differences/{difference_id}", response_model=DifferenceItem)
async def get_difference(
    difference_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> DifferenceItem:
    item = await DifferenceRepository(session).get(difference_id)
    if item is None or item.tenant_id != operator.tenant_id:
        raise HTTPException(404, detail="difference not found")
    return item


async def _require_task(
    session: AsyncSession,
    task_id: UUID,
    operator: OperatorContext,
) -> None:
    task = await TaskRepository(session).get(task_id)
    if task is None or task.tenant_id != operator.tenant_id:
        raise HTTPException(404, detail=f"reconciliation task not found: {task_id}")

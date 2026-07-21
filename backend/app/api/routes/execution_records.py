import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_operator_context, get_session
from app.core.security import OperatorContext
from app.executions.record_service import ExecutionRecordService
from app.schemas.executions import (
    ExecutionBatchStatus,
    ExecutionRecordDetail,
    ExecutionRecordPage,
)

router = APIRouter(prefix="/api/execution-records", tags=["execution-records"])


@router.get("", response_model=ExecutionRecordPage)
async def list_execution_records(
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    task_id: UUID | None = None,
    confirmed_by: str | None = None,
    status: ExecutionBatchStatus | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ExecutionRecordPage:
    return await ExecutionRecordService(session, operator=operator).list_records(
        task_id=task_id,
        confirmed_by=confirmed_by,
        status=status,
        created_from=created_from,
        created_to=created_to,
        cursor=cursor,
        limit=limit,
    )


@router.get("/{batch_id}", response_model=ExecutionRecordDetail)
async def get_execution_record(
    batch_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> ExecutionRecordDetail:
    try:
        return await ExecutionRecordService(session, operator=operator).get_detail(batch_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/{batch_id}/target-version")
async def download_execution_target(
    batch_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> FileResponse:
    try:
        version = await ExecutionRecordService(session, operator=operator).latest_output_version(
            batch_id
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    path = Path(version.storage_path)
    if not await asyncio.to_thread(path.is_file):
        raise HTTPException(status_code=404, detail="execution target file not found")
    return FileResponse(
        path,
        media_type="text/csv",
        filename=f"seewo-{version.id}.csv",
    )

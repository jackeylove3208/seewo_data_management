import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.job_service import AnalysisJobService, job_progress
from app.api.dependencies import get_operator_context, get_session
from app.core.security import OperatorContext
from app.repositories.analysis_jobs import AnalysisJobRepository
from app.schemas.analysis_jobs import AnalysisJobProgress, AnalysisJobStatus

router = APIRouter(prefix="/api", tags=["analysis-jobs"])

TERMINAL_JOB_STATUSES = {
    AnalysisJobStatus.COMPLETED,
    AnalysisJobStatus.COMPLETED_WITH_FAILURES,
    AnalysisJobStatus.CANCELED,
}


@router.post(
    "/reconciliation-tasks/{task_id}/analysis-jobs",
    response_model=AnalysisJobProgress,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_analysis_job(
    task_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> AnalysisJobProgress:
    try:
        job = await AnalysisJobService(session, operator=operator).create_job(
            task_id,
            idempotency_key=idempotency_key,
        )
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error
    return job_progress(job)


@router.get("/analysis-jobs/{job_id}", response_model=AnalysisJobProgress)
async def get_analysis_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AnalysisJobProgress:
    try:
        return await AnalysisJobService(session, operator=operator).progress(job_id)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error


@router.post("/analysis-jobs/{job_id}/retry", response_model=AnalysisJobProgress)
async def retry_analysis_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AnalysisJobProgress:
    service = AnalysisJobService(session, operator=operator)
    try:
        return job_progress(await service.retry(job_id))
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error


@router.post("/analysis-jobs/{job_id}/cancel", response_model=AnalysisJobProgress)
async def cancel_analysis_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AnalysisJobProgress:
    service = AnalysisJobService(session, operator=operator)
    try:
        return job_progress(await service.cancel(job_id))
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error


@router.get("/analysis-jobs/{job_id}/events")
async def stream_analysis_job_events(
    job_id: UUID,
    request: Request,
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    try:
        async with request.app.state.database.session_factory() as session:
            await AnalysisJobService(session, operator=operator).get(job_id)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error
    try:
        cursor = max(0, int(last_event_id or 0))
    except ValueError as error:
        raise HTTPException(400, detail="invalid Last-Event-ID") from error
    return StreamingResponse(
        _event_stream(
            request,
            job_id=job_id,
            tenant_id=operator.tenant_id,
            cursor=cursor,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _event_stream(
    request: Request,
    *,
    job_id: UUID,
    tenant_id: str,
    cursor: int,
) -> AsyncIterator[str]:
    last_sent = cursor
    idle_ticks = 0
    while not await request.is_disconnected():
        async with request.app.state.database.session_factory() as event_session:
            job = await AnalysisJobRepository(event_session).get_for_tenant(job_id, tenant_id)
            if job is None:
                return
            progress = job_progress(job)
            event_cursor = max(1, job.event_cursor)
        if event_cursor > last_sent:
            payload = json.dumps(
                progress.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"id: {event_cursor}\nevent: progress\ndata: {payload}\n\n"
            last_sent = event_cursor
            idle_ticks = 0
        else:
            idle_ticks += 1
            if idle_ticks >= 15:
                yield ": keepalive\n\n"
                idle_ticks = 0
        if progress.status in TERMINAL_JOB_STATUSES:
            return
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

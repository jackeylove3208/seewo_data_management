import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_operator_context, get_session
from app.core.security import OperatorContext
from app.models.mappings import EntityMapping
from app.models.rematching import EntityRematchJobRecord
from app.models.snapshots import Snapshot
from app.repositories.quality import MatchingQualityRepository
from app.repositories.rematching import EntityRematchRepository, RematchWorkItemDraft
from app.repositories.tasks import TaskRepository
from app.schemas.rematching_api import MatchingQualityResponse, RematchingJobResponse
from app.workflow.versioning import LegacyWorkflowOnlyError, require_legacy_workflow

router = APIRouter(prefix="/api", tags=["entity-rematching"])
TERMINAL = {"completed", "completed_with_failures", "canceled"}


def _response(job: EntityRematchJobRecord) -> RematchingJobResponse:
    return RematchingJobResponse(
        job_id=job.id,
        task_id=job.task_id,
        status=job.status,
        initial_unresolved=job.total,
        indexed=job.indexed,
        processed=job.processed,
        ai_recovered=job.ai_recovered,
        no_match=job.no_match,
        manual_review=job.manual_review,
        conflict=job.conflict,
        failed=job.failed,
        updated_at=job.heartbeat_at or job.completed_at or job.started_at or job.created_at,
    )


@router.post(
    "/reconciliation-tasks/{task_id}/entity-rematch-jobs",
    response_model=RematchingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_rematching_job(
    task_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> RematchingJobResponse:
    task = await TaskRepository(session).get(task_id)
    if task is None or task.tenant_id != operator.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="reconciliation task not found")
    try:
        require_legacy_workflow(task.workflow_version)
    except LegacyWorkflowOnlyError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
    source = await session.scalar(
        select(Snapshot).where(Snapshot.task_id == task_id, Snapshot.source_role == "authoritative")
    )
    target = await session.scalar(
        select(Snapshot).where(Snapshot.task_id == task_id, Snapshot.source_role == "target")
    )
    if source is None or target is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="published snapshot pair is required")
    rows = tuple(
        await session.scalars(
            select(EntityMapping).where(
                EntityMapping.task_id == task_id, EntityMapping.tenant_id == operator.tenant_id
            )
        )
    )
    drafts = tuple(
        RematchWorkItemDraft(
            entity_type=row.entity_type,
            focal_entity_id=row.source_entity_id,
            focal_role="authoritative",
            candidate_set_hash=hashlib.sha256(b"[]").hexdigest(),
            candidates=(),
        )
        for row in rows
        if row.status != "accepted"
    )
    job = await EntityRematchRepository(session).create_or_get(
        task_id=task_id,
        tenant_id=operator.tenant_id,
        requested_by=operator.operator_id,
        source_snapshot_id=source.id,
        target_snapshot_id=target.id,
        idempotency_key=idempotency_key,
        policy_version="rematching-v1",
        items=drafts,
    )
    return _response(job)


async def _owned_job(
    session: AsyncSession, job_id: UUID, operator: OperatorContext
) -> EntityRematchJobRecord:
    job = await EntityRematchRepository(session).get_for_tenant(job_id, operator.tenant_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="entity rematch job not found")
    return job


@router.get("/entity-rematch-jobs/{job_id}", response_model=RematchingJobResponse)
async def get_rematching_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> RematchingJobResponse:
    return _response(await _owned_job(session, job_id, operator))


@router.post(
    "/entity-rematch-jobs/{job_id}/retry",
    response_model=RematchingJobResponse,
)
async def retry_rematching_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> RematchingJobResponse:
    job = await _owned_job(session, job_id, operator)
    if job.status not in {"completed_with_failures", "canceled"}:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="entity rematch job is not retryable")
    job.cancel_requested = False
    job.status = "queued"
    job.completed_at = None
    for item in await EntityRematchRepository(session).work_items(job.id, operator.tenant_id):
        if item.status in {"failed", "manual_review", "no_match", "conflict", "canceled"}:
            item.status = "queued"
            item.outcome_status = None
            item.outcome = None
            item.completed_at = None
    await session.flush()
    return _response(job)


@router.post(
    "/entity-rematch-jobs/{job_id}/cancel",
    response_model=RematchingJobResponse,
)
async def cancel_rematching_job(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> RematchingJobResponse:
    job = await EntityRematchRepository(session).cancel(job_id, operator.tenant_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="entity rematch job not found")
    return _response(job)


@router.get("/entity-rematch-jobs/{job_id}/events")
async def stream_rematching_events(
    job_id: UUID,
    request: Request,
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    try:
        cursor = max(0, int(last_event_id or 0))
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid Last-Event-ID") from error
    async with request.app.state.database.session_factory() as session:
        await _owned_job(session, job_id, operator)
    return StreamingResponse(
        _event_stream(request, job_id, operator.tenant_id, cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _event_stream(
    request: Request, job_id: UUID, tenant_id: str, cursor: int
) -> AsyncIterator[str]:
    last = cursor
    while not await request.is_disconnected():
        async with request.app.state.database.session_factory() as session:
            job = await EntityRematchRepository(session).get_for_tenant(job_id, tenant_id)
            if job is None:
                return
            response = _response(job)
            event_cursor = job.event_cursor
        if event_cursor > last:
            payload = json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
            yield f"id: {event_cursor}\nevent: progress\ndata: {payload}\n\n"
            last = event_cursor
        if response.status in TERMINAL:
            return
        await asyncio.sleep(1)


@router.get(
    "/reconciliation-tasks/{task_id}/matching-quality",
    response_model=MatchingQualityResponse,
)
async def get_matching_quality(
    task_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> MatchingQualityResponse:
    task = await TaskRepository(session).get(task_id)
    if task is not None and task.tenant_id != operator.tenant_id:
        task = None
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="reconciliation task not found")
    try:
        require_legacy_workflow(task.workflow_version)
    except LegacyWorkflowOnlyError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
    record = await MatchingQualityRepository(session).latest(task_id, operator.tenant_id)
    if record is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "matching_quality_not_evaluated", "task_id": str(task_id)},
        )
    return MatchingQualityResponse.model_validate(record.result)

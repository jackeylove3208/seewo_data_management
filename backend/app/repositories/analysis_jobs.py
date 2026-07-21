from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis_jobs import AnalysisJobRecord, AnalysisWorkItemRecord
from app.schemas.analysis_jobs import AnalysisJobStatus, AnalysisWorkItemStatus
from app.schemas.governance import ResolutionMode


class AnalysisJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, job_id: UUID) -> AnalysisJobRecord | None:
        return await self.session.get(AnalysisJobRecord, job_id)

    async def get_for_tenant(self, job_id: UUID, tenant_id: str) -> AnalysisJobRecord | None:
        return cast(
            AnalysisJobRecord | None,
            await self.session.scalar(
                select(AnalysisJobRecord).where(
                    AnalysisJobRecord.id == job_id,
                    AnalysisJobRecord.tenant_id == tenant_id,
                )
            ),
        )

    async def current_for_task(
        self,
        task_id: UUID,
        tenant_id: str,
    ) -> AnalysisJobRecord | None:
        return cast(
            AnalysisJobRecord | None,
            await self.session.scalar(
                select(AnalysisJobRecord)
                .where(
                    AnalysisJobRecord.task_id == task_id,
                    AnalysisJobRecord.tenant_id == tenant_id,
                )
                .order_by(AnalysisJobRecord.created_at.desc(), AnalysisJobRecord.id.desc())
            ),
        )

    async def create_or_get(
        self,
        *,
        task_id: UUID,
        tenant_id: str,
        requested_by: str,
        idempotency_key: str,
        difference_versions: tuple[tuple[UUID, int], ...],
        analysis_version: str = "analysis-v3",
    ) -> AnalysisJobRecord:
        existing = cast(
            AnalysisJobRecord | None,
            await self.session.scalar(
                select(AnalysisJobRecord).where(
                    AnalysisJobRecord.task_id == task_id,
                    AnalysisJobRecord.tenant_id == tenant_id,
                    AnalysisJobRecord.idempotency_key == idempotency_key,
                )
            ),
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        job = AnalysisJobRecord(
            task_id=task_id,
            tenant_id=tenant_id,
            requested_by=requested_by,
            analysis_version=analysis_version,
            status=AnalysisJobStatus.QUEUED.value,
            total=len(difference_versions),
            idempotency_key=idempotency_key,
            created_at=now,
            event_cursor=1,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(job)
                await self.session.flush()
                self.session.add_all(
                    AnalysisWorkItemRecord(
                        job_id=job.id,
                        tenant_id=tenant_id,
                        difference_id=difference_id,
                        difference_version=difference_version,
                        status=AnalysisWorkItemStatus.QUEUED.value,
                        available_at=now,
                        created_at=now,
                    )
                    for difference_id, difference_version in difference_versions
                )
                await self.session.flush()
        except IntegrityError:
            existing = cast(
                AnalysisJobRecord | None,
                await self.session.scalar(
                    select(AnalysisJobRecord).where(
                        AnalysisJobRecord.task_id == task_id,
                        AnalysisJobRecord.tenant_id == tenant_id,
                        AnalysisJobRecord.idempotency_key == idempotency_key,
                    )
                ),
            )
            if existing is None:
                raise
            return existing
        return job

    async def claim_next(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> AnalysisWorkItemRecord | None:
        now = datetime.now(UTC)
        item = await self.session.scalar(
            select(AnalysisWorkItemRecord)
            .where(
                AnalysisWorkItemRecord.job_id == job_id,
                or_(
                    and_(
                        AnalysisWorkItemRecord.status.in_(
                            (
                                AnalysisWorkItemStatus.QUEUED.value,
                                AnalysisWorkItemStatus.RETRY_WAIT.value,
                            )
                        ),
                        AnalysisWorkItemRecord.available_at <= now,
                        or_(
                            AnalysisWorkItemRecord.lease_expires_at.is_(None),
                            AnalysisWorkItemRecord.lease_expires_at < now,
                        ),
                    ),
                    and_(
                        AnalysisWorkItemRecord.status == AnalysisWorkItemStatus.RUNNING.value,
                        AnalysisWorkItemRecord.lease_expires_at < now,
                    ),
                ),
            )
            .order_by(AnalysisWorkItemRecord.available_at, AnalysisWorkItemRecord.id)
            .with_for_update(skip_locked=True)
        )
        if item is None:
            return None
        job = await self.session.scalar(
            select(AnalysisJobRecord).where(AnalysisJobRecord.id == job_id).with_for_update()
        )
        if job is None or job.cancel_requested:
            item.status = AnalysisWorkItemStatus.CANCELED.value
            item.completed_at = now
            await self.session.flush()
            return None
        item.status = AnalysisWorkItemStatus.RUNNING.value
        item.attempt_count += 1
        item.lease_owner = worker_id
        item.lease_expires_at = now + timedelta(seconds=lease_seconds)
        item.heartbeat_at = now
        item.started_at = item.started_at or now
        if job.status == AnalysisJobStatus.QUEUED.value:
            job.status = AnalysisJobStatus.RUNNING.value
            job.started_at = now
        job.heartbeat_at = now
        job.event_cursor += 1
        await self.session.flush()
        return item

    async def claim_next_available(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> AnalysisWorkItemRecord | None:
        job_ids = await self.session.scalars(
            select(AnalysisJobRecord.id)
            .where(
                AnalysisJobRecord.status.in_(
                    (AnalysisJobStatus.QUEUED.value, AnalysisJobStatus.RUNNING.value)
                ),
                AnalysisJobRecord.cancel_requested.is_(False),
            )
            .order_by(AnalysisJobRecord.created_at, AnalysisJobRecord.id)
        )
        for job_id in job_ids:
            item = await self.claim_next(
                job_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
            if item is not None:
                return item
        return None

    async def heartbeat(self, item_id: UUID, *, worker_id: str, lease_seconds: int) -> bool:
        now = datetime.now(UTC)
        item = await self.session.scalar(
            select(AnalysisWorkItemRecord)
            .where(AnalysisWorkItemRecord.id == item_id)
            .with_for_update()
        )
        if item is None or item.status != AnalysisWorkItemStatus.RUNNING.value:
            return False
        if item.lease_owner != worker_id:
            return False
        if _lease_expired(item.lease_expires_at, now):
            return False
        item.lease_expires_at = now + timedelta(seconds=lease_seconds)
        item.heartbeat_at = now
        job = await self.session.scalar(
            select(AnalysisJobRecord).where(AnalysisJobRecord.id == item.job_id).with_for_update()
        )
        if job is not None:
            job.heartbeat_at = now
            job.event_cursor += 1
        await self.session.flush()
        return True

    async def complete_item(
        self,
        item_id: UUID,
        *,
        worker_id: str,
        outcome: AnalysisWorkItemStatus,
        result_id: UUID | None = None,
        failure_code: str | None = None,
        fallback: dict[str, object] | None = None,
        resolution_mode: ResolutionMode | None = None,
    ) -> None:
        now = datetime.now(UTC)
        item = await self.session.scalar(
            select(AnalysisWorkItemRecord)
            .where(AnalysisWorkItemRecord.id == item_id)
            .with_for_update()
        )
        if item is None:
            raise LookupError(f"analysis work item not found: {item_id}")
        if item.status != AnalysisWorkItemStatus.RUNNING.value or item.lease_owner != worker_id:
            raise ValueError("analysis work item lease is no longer owned by worker")
        if _lease_expired(item.lease_expires_at, now):
            raise ValueError("analysis work item lease has expired")
        if outcome not in {
            AnalysisWorkItemStatus.SUCCEEDED,
            AnalysisWorkItemStatus.MANUAL_REQUIRED,
            AnalysisWorkItemStatus.FAILED,
            AnalysisWorkItemStatus.SUPERSEDED,
            AnalysisWorkItemStatus.CANCELED,
        }:
            raise ValueError("item completion requires a terminal outcome")
        item.status = outcome.value
        item.result_id = result_id
        item.failure_code = failure_code
        item.fallback = fallback
        item.completed_at = now
        item.lease_owner = None
        item.lease_expires_at = None
        item.heartbeat_at = now
        job = await self.session.scalar(
            select(AnalysisJobRecord).where(AnalysisJobRecord.id == item.job_id).with_for_update()
        )
        if job is None:
            raise LookupError(f"analysis job not found: {item.job_id}")
        if outcome is not AnalysisWorkItemStatus.CANCELED:
            job.completed += 1
        if outcome is AnalysisWorkItemStatus.SUCCEEDED:
            job.succeeded += 1
            mode = resolution_mode or ResolutionMode.AUTO_EXECUTABLE
            if mode is not ResolutionMode.AUTO_EXECUTABLE:
                raise ValueError("succeeded work item requires an executable resolution")
            item.resolution_mode = mode.value
            if mode is ResolutionMode.AUTO_EXECUTABLE:
                job.proposal_ready += 1
        elif outcome is AnalysisWorkItemStatus.MANUAL_REQUIRED:
            job.manual_required += 1
            mode = resolution_mode or ResolutionMode.MANUAL_ONLY
            if mode is ResolutionMode.AUTO_EXECUTABLE:
                raise ValueError("manual-required work item requires a non-executable resolution")
            item.resolution_mode = mode.value
            if mode is ResolutionMode.NEEDS_INFORMATION:
                job.needs_information += 1
            else:
                job.manual_only += 1
        elif outcome in {
            AnalysisWorkItemStatus.FAILED,
            AnalysisWorkItemStatus.SUPERSEDED,
        }:
            job.failed += 1
            job.last_error = failure_code
        job.event_cursor += 1
        if (
            job.completed >= job.total
            and not job.cancel_requested
            and job.status != AnalysisJobStatus.CANCELED.value
        ):
            job.completed_at = now
            job.status = (
                AnalysisJobStatus.COMPLETED_WITH_FAILURES.value
                if job.failed
                else AnalysisJobStatus.COMPLETED.value
            )
        await self.session.flush()

    async def schedule_retry(
        self,
        item_id: UUID,
        *,
        worker_id: str,
        available_at: datetime,
        failure_code: str,
    ) -> None:
        item = await self.session.scalar(
            select(AnalysisWorkItemRecord)
            .where(AnalysisWorkItemRecord.id == item_id)
            .with_for_update()
        )
        now = datetime.now(UTC)
        if item is None or item.lease_owner != worker_id:
            raise ValueError("analysis work item lease is no longer owned by worker")
        if _lease_expired(item.lease_expires_at, now):
            raise ValueError("analysis work item lease has expired")
        item.status = AnalysisWorkItemStatus.RETRY_WAIT.value
        item.available_at = available_at
        item.failure_code = failure_code
        item.resolution_mode = None
        item.lease_owner = None
        item.lease_expires_at = None
        job = await self.session.scalar(
            select(AnalysisJobRecord).where(AnalysisJobRecord.id == item.job_id).with_for_update()
        )
        if job is not None:
            job.event_cursor += 1
        await self.session.flush()

    async def cancel(self, job_id: UUID) -> AnalysisJobRecord | None:
        job = await self.session.scalar(
            select(AnalysisJobRecord).where(AnalysisJobRecord.id == job_id).with_for_update()
        )
        if job is None:
            return None
        job.cancel_requested = True
        if job.status in {
            AnalysisJobStatus.QUEUED.value,
            AnalysisJobStatus.RUNNING.value,
        }:
            job.status = AnalysisJobStatus.CANCELED.value
            job.completed_at = datetime.now(UTC)
        job.event_cursor += 1
        await self.session.flush()
        return job

    async def retry_failed(self, job_id: UUID) -> AnalysisJobRecord | None:
        job = await self.session.scalar(
            select(AnalysisJobRecord).where(AnalysisJobRecord.id == job_id).with_for_update()
        )
        if job is None:
            return None
        items = tuple(
            await self.session.scalars(
                select(AnalysisWorkItemRecord)
                .where(
                    AnalysisWorkItemRecord.job_id == job_id,
                    AnalysisWorkItemRecord.status.in_(
                        (
                            AnalysisWorkItemStatus.FAILED.value,
                            AnalysisWorkItemStatus.CANCELED.value,
                        )
                    ),
                )
                .with_for_update()
            )
        )
        was_canceled = job.status == AnalysisJobStatus.CANCELED.value
        if not items and not was_canceled:
            return job
        now = datetime.now(UTC)
        failed_count = sum(
            item.status == AnalysisWorkItemStatus.FAILED.value for item in items
        )
        for item in items:
            item.status = AnalysisWorkItemStatus.QUEUED.value
            item.available_at = now
            item.lease_owner = None
            item.lease_expires_at = None
            item.completed_at = None
            item.failure_code = None
            item.resolution_mode = None
            item.fallback = None
        job.completed -= failed_count
        job.failed -= failed_count
        if job.completed >= job.total:
            job.status = (
                AnalysisJobStatus.COMPLETED_WITH_FAILURES.value
                if job.failed
                else AnalysisJobStatus.COMPLETED.value
            )
        elif job.completed:
            job.status = AnalysisJobStatus.RUNNING.value
        else:
            job.status = AnalysisJobStatus.QUEUED.value
        job.completed_at = None
        job.last_error = None
        job.cancel_requested = False
        job.event_cursor += 1
        await self.session.flush()
        return job

    async def recover_expired_leases(self, *, now: datetime | None = None) -> int:
        recovered_at = now or datetime.now(UTC)
        items = tuple(
            await self.session.scalars(
                select(AnalysisWorkItemRecord)
                .where(
                    AnalysisWorkItemRecord.status == AnalysisWorkItemStatus.RUNNING.value,
                    AnalysisWorkItemRecord.lease_expires_at < recovered_at,
                )
                .with_for_update(skip_locked=True)
            )
        )
        touched_jobs: set[UUID] = set()
        for item in items:
            item.status = AnalysisWorkItemStatus.QUEUED.value
            item.available_at = recovered_at
            item.lease_owner = None
            item.lease_expires_at = None
            item.failure_code = "lease_expired"
            item.resolution_mode = None
            item.heartbeat_at = recovered_at
            touched_jobs.add(item.job_id)
        for job_id in touched_jobs:
            job = await self.session.scalar(
                select(AnalysisJobRecord).where(AnalysisJobRecord.id == job_id).with_for_update()
            )
            if job is not None:
                job.heartbeat_at = recovered_at
                job.event_cursor += 1
        await self.session.flush()
        return len(items)

    async def reconcile_counters(self, job_id: UUID) -> AnalysisJobRecord | None:
        items = tuple(
            await self.session.scalars(
                select(AnalysisWorkItemRecord)
                .where(AnalysisWorkItemRecord.job_id == job_id)
                .with_for_update()
            )
        )
        job = await self.session.scalar(
            select(AnalysisJobRecord).where(AnalysisJobRecord.id == job_id).with_for_update()
        )
        if job is None:
            return None
        succeeded = sum(item.status == AnalysisWorkItemStatus.SUCCEEDED.value for item in items)
        manual_required = sum(
            item.status == AnalysisWorkItemStatus.MANUAL_REQUIRED.value for item in items
        )
        failed = sum(
            item.status
            in {
                AnalysisWorkItemStatus.FAILED.value,
                AnalysisWorkItemStatus.SUPERSEDED.value,
            }
            for item in items
        )
        job.succeeded = succeeded
        job.manual_required = manual_required
        job.failed = failed
        job.completed = succeeded + manual_required + failed
        job.proposal_ready = sum(
            item.status == AnalysisWorkItemStatus.SUCCEEDED.value
            and item.resolution_mode == ResolutionMode.AUTO_EXECUTABLE.value
            for item in items
        )
        job.needs_information = sum(
            item.status == AnalysisWorkItemStatus.MANUAL_REQUIRED.value
            and item.resolution_mode == ResolutionMode.NEEDS_INFORMATION.value
            for item in items
        )
        job.manual_only = sum(
            item.status == AnalysisWorkItemStatus.MANUAL_REQUIRED.value
            and item.resolution_mode == ResolutionMode.MANUAL_ONLY.value
            for item in items
        )
        if not job.cancel_requested:
            if job.completed >= job.total:
                job.status = (
                    AnalysisJobStatus.COMPLETED_WITH_FAILURES.value
                    if job.failed
                    else AnalysisJobStatus.COMPLETED.value
                )
                job.completed_at = datetime.now(UTC)
            else:
                job.status = (
                    AnalysisJobStatus.RUNNING.value
                    if job.completed
                    else AnalysisJobStatus.QUEUED.value
                )
                job.completed_at = None
        job.event_cursor += 1
        await self.session.flush()
        return job


def _lease_expired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now

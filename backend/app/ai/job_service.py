from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import OperatorContext
from app.models.analysis_jobs import AnalysisJobRecord
from app.repositories.analysis_jobs import AnalysisJobRepository
from app.repositories.differences import DifferenceRepository
from app.repositories.tasks import TaskRepository
from app.repositories.workflow import WorkflowRunRepository
from app.schemas.analysis_jobs import AnalysisJobProgress, AnalysisJobStatus
from app.schemas.workflow import WorkflowError, WorkflowStatus


class AnalysisJobService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        operator: OperatorContext,
        jobs: AnalysisJobRepository | None = None,
        differences: DifferenceRepository | None = None,
    ) -> None:
        self.session = session
        self.operator = operator
        self.jobs = jobs or AnalysisJobRepository(session)
        self.differences = differences or DifferenceRepository(session)
        self.tasks = TaskRepository(session)
        self.runs = WorkflowRunRepository(session)

    async def create_job(
        self,
        task_id: UUID,
        *,
        idempotency_key: str,
    ) -> AnalysisJobRecord:
        task = await self.tasks.get(task_id)
        if task is None or task.tenant_id != self.operator.tenant_id:
            raise LookupError(f"reconciliation task not found: {task_id}")
        differences = await self.differences.for_task(task_id)
        job = await self.jobs.create_or_get(
            task_id=task_id,
            tenant_id=self.operator.tenant_id,
            requested_by=self.operator.operator_id,
            idempotency_key=idempotency_key,
            difference_versions=tuple(
                (difference.id, difference.version) for difference in differences
            ),
        )
        if job.total == 0 and job.status == AnalysisJobStatus.QUEUED.value:
            now = datetime.now(UTC)
            job.status = AnalysisJobStatus.COMPLETED.value
            job.completed_at = now
            job.event_cursor += 1
            await self.session.flush()
        return job

    async def get(self, job_id: UUID) -> AnalysisJobRecord:
        job = await self.jobs.get_for_tenant(job_id, self.operator.tenant_id)
        if job is None:
            raise LookupError(f"analysis job not found: {job_id}")
        return job

    async def progress(self, job_id: UUID) -> AnalysisJobProgress:
        return job_progress(await self.get(job_id))

    async def cancel(self, job_id: UUID) -> AnalysisJobRecord:
        await self.get(job_id)
        canceled = await self.jobs.cancel(job_id)
        if canceled is None:
            raise LookupError(f"analysis job not found: {job_id}")
        await self.sync_workflow(canceled)
        return canceled

    async def retry(self, job_id: UUID) -> AnalysisJobRecord:
        await self.get(job_id)
        retried = await self.jobs.retry_failed(job_id)
        if retried is None:
            raise LookupError(f"analysis job not found: {job_id}")
        await self.sync_workflow(retried)
        return retried

    async def sync_workflow(self, job: AnalysisJobRecord) -> None:
        task = await self.tasks.get_for_update(job.task_id)
        if task is None or task.tenant_id != job.tenant_id:
            raise LookupError(f"reconciliation task not found: {job.task_id}")
        run = await self.runs.latest(job.task_id)
        if run is None or run.analysis_job_id != job.id:
            return
        run.processed = job.completed
        run.total = job.total
        run.succeeded = job.succeeded
        run.manual_review = job.manual_required
        run.failed = job.failed
        if job.status in {
            AnalysisJobStatus.COMPLETED.value,
            AnalysisJobStatus.COMPLETED_WITH_FAILURES.value,
        }:
            await self.runs.complete(
                run,
                processed=job.completed,
                total=job.total,
                succeeded=job.succeeded,
                manual_review=job.manual_required,
                failed=job.failed,
            )
            task.stage = "analysis_ready"
            task.status = "ready"
            task.error = None
        elif job.status == AnalysisJobStatus.CANCELED.value:
            error = WorkflowError(
                code="analysis_job_canceled",
                message="AI 分析作业已取消，可重试以继续处理。",
                retryable=True,
            )
            await self.runs.fail(run, error)
            task.stage = "differences_ready"
            task.status = "failed"
            task.error = error.model_dump(mode="json")
        elif job.status in {
            AnalysisJobStatus.QUEUED.value,
            AnalysisJobStatus.RUNNING.value,
        }:
            run.status = WorkflowStatus.RUNNING.value
            run.error = None
            run.retryable = False
            run.completed_at = None
            task.stage = "differences_ready"
            task.status = "processing"
            task.error = None
        await self.session.flush()


def job_progress(job: AnalysisJobRecord) -> AnalysisJobProgress:
    updated_at = job.heartbeat_at or job.completed_at or job.started_at or job.created_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return AnalysisJobProgress(
        job_id=job.id,
        task_id=job.task_id,
        status=AnalysisJobStatus(job.status),
        total=job.total,
        completed=job.completed,
        succeeded=job.succeeded,
        manual_required=job.manual_required,
        needs_information=job.needs_information,
        manual_only=job.manual_only,
        failed=job.failed,
        proposal_ready=job.proposal_ready,
        last_error=job.last_error,
        updated_at=updated_at,
    )

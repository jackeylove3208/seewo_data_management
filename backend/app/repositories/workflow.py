from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reconciliation import ReconciliationTask
from app.models.workflow import WorkflowStageRun
from app.schemas.workflow import (
    AnalysisProgress,
    WorkflowError,
    WorkflowStage,
    WorkflowState,
    WorkflowStatus,
)


class WorkflowRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(
        self,
        task_id: UUID,
        stage: WorkflowStage,
        *,
        total: int = 0,
    ) -> WorkflowStageRun:
        latest_attempt = await self.session.scalar(
            select(func.max(WorkflowStageRun.attempt)).where(
                WorkflowStageRun.task_id == task_id,
                WorkflowStageRun.stage == stage.value,
            )
        )
        run = WorkflowStageRun(
            task_id=task_id,
            stage=stage.value,
            attempt=(latest_attempt or 0) + 1,
            status=WorkflowStatus.RUNNING.value,
            total=total,
            started_at=datetime.now(UTC),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(run)
                await self.session.flush()
        except IntegrityError as error:
            raise ConcurrentStageRunError("workflow stage is already advancing") from error
        return run

    async def complete(
        self,
        run: WorkflowStageRun,
        *,
        processed: int = 0,
        total: int = 0,
        succeeded: int = 0,
        manual_review: int = 0,
        failed: int = 0,
    ) -> None:
        run.status = WorkflowStatus.SUCCEEDED.value
        run.processed = processed
        run.total = total
        run.succeeded = succeeded
        run.manual_review = manual_review
        run.failed = failed
        run.completed_at = datetime.now(UTC)
        run.error = None
        run.retryable = False
        await self.session.flush()

    async def fail(self, run: WorkflowStageRun, error: WorkflowError) -> None:
        run.status = WorkflowStatus.FAILED.value
        run.error = error.model_dump(mode="json")
        run.retryable = error.retryable
        run.completed_at = datetime.now(UTC)
        await self.session.flush()

    async def list_attempts(
        self,
        task_id: UUID,
        stage: WorkflowStage,
    ) -> tuple[WorkflowStageRun, ...]:
        rows = await self.session.scalars(
            select(WorkflowStageRun)
            .where(
                WorkflowStageRun.task_id == task_id,
                WorkflowStageRun.stage == stage.value,
            )
            .order_by(WorkflowStageRun.attempt, WorkflowStageRun.id)
        )
        return tuple(rows)

    async def latest(self, task_id: UUID) -> WorkflowStageRun | None:
        return cast(
            WorkflowStageRun | None,
            await self.session.scalar(
                select(WorkflowStageRun)
                .where(WorkflowStageRun.task_id == task_id)
                .order_by(WorkflowStageRun.started_at.desc(), WorkflowStageRun.id.desc())
            ),
        )

    async def state(self, task: ReconciliationTask) -> WorkflowState:
        latest = await self.latest(task.id)
        if task.status == "failed" and latest is not None:
            error = WorkflowError.model_validate(latest.error) if latest.error else None
            return WorkflowState(
                stage=WorkflowStage(latest.stage),
                status=WorkflowStatus.FAILED,
                attempt=latest.attempt,
                processed=latest.processed,
                total=latest.total,
                analysis=_analysis_progress(latest),
                error=error,
            )
        stage = _next_stage(task.stage)
        if stage is WorkflowStage.COMPLETE:
            analysis = _analysis_progress(latest) if latest is not None else AnalysisProgress()
            return WorkflowState(
                stage=stage,
                status=WorkflowStatus.SUCCEEDED,
                attempt=latest.attempt if latest is not None else 0,
                processed=latest.processed if latest is not None else 0,
                total=latest.total if latest is not None else 0,
                analysis=analysis,
            )
        analysis = (
            _analysis_progress(latest)
            if stage is WorkflowStage.ANALYSIS and latest is not None
            else AnalysisProgress()
        )
        return WorkflowState(
            stage=stage,
            status=WorkflowStatus.PENDING,
            attempt=latest.attempt if latest is not None else 0,
            processed=analysis.completed if stage is WorkflowStage.ANALYSIS else 0,
            total=analysis.total if stage is WorkflowStage.ANALYSIS else 0,
            analysis=analysis,
        )


def _next_stage(task_stage: str) -> WorkflowStage:
    try:
        return {
            "ingestion": WorkflowStage.INGESTION,
            "snapshots": WorkflowStage.MATCHING,
            "matching": WorkflowStage.DIFFERENCES,
            "differences_ready": WorkflowStage.ANALYSIS,
            "analysis_ready": WorkflowStage.COMPLETE,
        }[task_stage]
    except KeyError as error:
        raise ValueError(f"unsupported reconciliation task stage: {task_stage}") from error


def _analysis_progress(run: WorkflowStageRun) -> AnalysisProgress:
    if run.stage != WorkflowStage.ANALYSIS.value:
        return AnalysisProgress()
    completed = run.succeeded + run.manual_review + run.failed
    return AnalysisProgress(
        total=run.total,
        completed=completed,
        succeeded=run.succeeded,
        manual_review=run.manual_review,
        failed=run.failed,
    )


class ConcurrentStageRunError(RuntimeError):
    pass

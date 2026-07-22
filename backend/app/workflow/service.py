import hashlib
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import OperatorContext
from app.models.analysis_jobs import AnalysisJobRecord
from app.models.snapshots import Snapshot
from app.repositories.rematching import EntityRematchRepository, RematchWorkItemDraft
from app.repositories.tasks import TaskRepository
from app.repositories.workflow import ConcurrentStageRunError, WorkflowRunRepository
from app.schemas.workflow import (
    WorkflowAdvanceResponse,
    WorkflowError,
    WorkflowStage,
)
from app.workflow.versioning import require_legacy_workflow


class ResolutionRunner(Protocol):
    async def resolve_task(self, task_id: UUID) -> object: ...


class DifferenceRunner(Protocol):
    async def detect(self, task_id: UUID) -> object: ...


class AnalysisJobRunner(Protocol):
    async def create_job(
        self,
        task_id: UUID,
        *,
        idempotency_key: str,
    ) -> AnalysisJobRecord: ...


class ReconciliationWorkflowService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        operator: OperatorContext,
        resolver: ResolutionRunner,
        detector: DifferenceRunner,
        analyzer: AnalysisJobRunner,
        runs: WorkflowRunRepository | None = None,
        analysis_batch_size: int = 10,
        rematching_enabled: bool = False,
    ) -> None:
        self.session = session
        self.operator = operator
        self.resolver = resolver
        self.detector = detector
        self.analyzer = analyzer
        self.tasks = TaskRepository(session)
        self.runs = runs or WorkflowRunRepository(session)
        self.analysis_batch_size = analysis_batch_size
        self.rematching_enabled = rematching_enabled

    async def advance(self, task_id: UUID) -> WorkflowAdvanceResponse:
        task = await self.tasks.get_for_update(task_id)
        if task is None or task.tenant_id != self.operator.tenant_id:
            raise LookupError(f"reconciliation task not found: {task_id}")
        require_legacy_workflow(task.workflow_version)
        if task.status == "failed":
            state = await self.runs.state(task)
            if not state.can_retry:
                raise ValueError("workflow failure is not retryable")
            raise ValueError("retryable workflow failure requires the retry command")

        if task.stage == "analysis_ready":
            return WorkflowAdvanceResponse(task_id=task.id, workflow=await self.runs.state(task))

        if self.rematching_enabled and task.stage == "matching":
            rematch = await EntityRematchRepository(self.session).current_for_task(
                task.id, self.operator.tenant_id
            )
            if rematch is not None and rematch.status not in {
                "completed",
                "completed_with_failures",
                "canceled",
            }:
                return WorkflowAdvanceResponse(
                    task_id=task.id, workflow=await self.runs.state(task)
                )

        stage = _stage_for_task(task.stage)
        try:
            run = await self.runs.start(task.id, stage)
        except ConcurrentStageRunError:
            await self.session.refresh(task)
            return WorkflowAdvanceResponse(task_id=task.id, workflow=await self.runs.state(task))
        task.status = "processing"
        task.error = None
        await self.session.flush()
        try:
            if stage is WorkflowStage.MATCHING:
                summary = await self.resolver.resolve_task(task.id)
                unresolved = tuple(
                    decision
                    for decision in getattr(summary, "decisions", ())
                    if getattr(decision.status, "value", decision.status) != "accepted"
                )
                if self.rematching_enabled and unresolved:
                    source = await self.session.scalar(
                        select(Snapshot).where(
                            Snapshot.task_id == task.id,
                            Snapshot.source_role == "authoritative",
                        )
                    )
                    target = await self.session.scalar(
                        select(Snapshot).where(
                            Snapshot.task_id == task.id,
                            Snapshot.source_role == "target",
                        )
                    )
                    if source is not None and target is not None:
                        drafts = tuple(
                            RematchWorkItemDraft(
                                entity_type=decision.entity_type.value,
                                focal_entity_id=decision.source_entity_id,
                                focal_role="authoritative",
                                candidate_set_hash=hashlib.sha256(b"[]").hexdigest(),
                                candidates=(),
                            )
                            for decision in unresolved
                        )
                        await EntityRematchRepository(self.session).create_or_get(
                            task_id=task.id,
                            tenant_id=task.tenant_id,
                            requested_by=self.operator.operator_id,
                            source_snapshot_id=source.id,
                            target_snapshot_id=target.id,
                            idempotency_key=f"workflow-rematching-v1:{task.id}",
                            policy_version="rematching-v1",
                            items=drafts,
                        )
                task.stage = "matching"
                await self.runs.complete(run)
            elif stage is WorkflowStage.DIFFERENCES:
                result = await self.detector.detect(task.id)
                count = len(getattr(result, "difference_ids", ()))
                task.stage = "differences_ready"
                await self.runs.complete(run, processed=count, total=count)
            elif stage is WorkflowStage.ANALYSIS:
                job = await self.analyzer.create_job(
                    task.id,
                    idempotency_key=f"workflow-analysis-v3:{task.id}",
                )
                run.analysis_job_id = job.id
                run.processed = getattr(job, "completed", 0)
                run.total = getattr(job, "total", 0)
                run.succeeded = getattr(job, "succeeded", 0)
                run.manual_review = getattr(job, "manual_required", 0)
                run.failed = getattr(job, "failed", 0)
                await self.session.flush()
                task.status = "processing"
                return WorkflowAdvanceResponse(
                    task_id=task.id,
                    workflow=await self.runs.state(task),
                )
            else:
                raise ValueError(f"workflow cannot advance stage: {stage.value}")
        except Exception as error:
            workflow_error = _workflow_error(error)
            run_id = run.id
            run_attempt = run.attempt
            run_started_at = run.started_at
            if self.session.is_active and not isinstance(error, DBAPIError):
                await self.runs.fail(run, workflow_error)
            else:
                await self.session.rollback()
                task = await self.tasks.get_for_update(task_id)
                if task is None:
                    raise LookupError(f"reconciliation task not found: {task_id}") from error
                failed_run = await self.runs.fail_after_rollback(
                    task_id,
                    stage,
                    run_attempt,
                    run_id,
                    run_started_at,
                    workflow_error,
                )
                if failed_run is None:
                    return WorkflowAdvanceResponse(
                        task_id=task.id,
                        workflow=await self.runs.state(task),
                    )
            task.status = "failed"
            task.error = workflow_error.model_dump(mode="json")
            await self.session.flush()
            return WorkflowAdvanceResponse(task_id=task.id, workflow=await self.runs.state(task))

        task.status = "ready"
        await self.session.flush()
        return WorkflowAdvanceResponse(task_id=task.id, workflow=await self.runs.state(task))

    async def retry(self, task_id: UUID) -> WorkflowAdvanceResponse:
        task = await self.tasks.get_for_update(task_id)
        if task is None or task.tenant_id != self.operator.tenant_id:
            raise LookupError(f"reconciliation task not found: {task_id}")
        require_legacy_workflow(task.workflow_version)
        state = await self.runs.state(task)
        if not state.can_retry:
            raise ValueError("workflow failure is not retryable")
        task.status = "ready"
        task.error = None
        await self.session.flush()
        return await self.advance(task_id)


def _stage_for_task(task_stage: str) -> WorkflowStage:
    try:
        return {
            "snapshots": WorkflowStage.MATCHING,
            "matching": WorkflowStage.DIFFERENCES,
            "differences_ready": WorkflowStage.ANALYSIS,
        }[task_stage]
    except KeyError as error:
        raise ValueError(f"workflow cannot advance task stage: {task_stage}") from error


def _workflow_error(error: Exception) -> WorkflowError:
    schema_message = _schema_mismatch_message(error)
    if schema_message is not None:
        return WorkflowError(
            code="database_schema_mismatch",
            message=schema_message,
            retryable=True,
        )
    retryable = isinstance(error, (ConnectionError, TimeoutError)) or (
        isinstance(error, DBAPIError) and error.connection_invalidated
    )
    return WorkflowError(
        code="workflow_timeout" if retryable else "workflow_stage_failed",
        message=str(error) or type(error).__name__,
        retryable=retryable,
    )


def _schema_mismatch_message(error: Exception) -> str | None:
    detail = str(getattr(error, "orig", error))
    normalized = detail.casefold()
    if "analysis_results.gateway_request_ids" not in normalized:
        return None
    if not any(
        marker in normalized for marker in ("no such column", "does not exist", "undefined column")
    ):
        return None
    return (
        "Database schema is missing analysis_results.gateway_request_ids. "
        "Run `alembic upgrade head` before retrying. "
        f"Original database error: {detail}"
    )

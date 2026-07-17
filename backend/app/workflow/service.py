from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import OperatorContext
from app.repositories.tasks import TaskRepository
from app.repositories.workflow import ConcurrentStageRunError, WorkflowRunRepository
from app.schemas.governance import AnalysisBatchResponse
from app.schemas.workflow import (
    WorkflowAdvanceResponse,
    WorkflowError,
    WorkflowStage,
)


class ResolutionRunner(Protocol):
    async def resolve_task(self, task_id: UUID) -> object: ...


class DifferenceRunner(Protocol):
    async def detect(self, task_id: UUID) -> object: ...


class AnalysisRunner(Protocol):
    async def analyze_batch(self, task_id: UUID, *, limit: int) -> AnalysisBatchResponse: ...


class ReconciliationWorkflowService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        operator: OperatorContext,
        resolver: ResolutionRunner,
        detector: DifferenceRunner,
        analyzer: AnalysisRunner,
        runs: WorkflowRunRepository | None = None,
        analysis_batch_size: int = 10,
    ) -> None:
        self.session = session
        self.operator = operator
        self.resolver = resolver
        self.detector = detector
        self.analyzer = analyzer
        self.tasks = TaskRepository(session)
        self.runs = runs or WorkflowRunRepository(session)
        self.analysis_batch_size = analysis_batch_size

    async def advance(self, task_id: UUID) -> WorkflowAdvanceResponse:
        task = await self.tasks.get_for_update(task_id)
        if task is None or task.tenant_id != self.operator.tenant_id:
            raise LookupError(f"reconciliation task not found: {task_id}")
        if task.status == "failed":
            state = await self.runs.state(task)
            if not state.can_retry:
                raise ValueError("workflow failure is not retryable")
            raise ValueError("retryable workflow failure requires the retry command")

        if task.stage == "analysis_ready":
            return WorkflowAdvanceResponse(task_id=task.id, workflow=await self.runs.state(task))

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
                await self.resolver.resolve_task(task.id)
                task.stage = "matching"
                await self.runs.complete(run)
            elif stage is WorkflowStage.DIFFERENCES:
                result = await self.detector.detect(task.id)
                count = len(getattr(result, "difference_ids", ()))
                task.stage = "differences_ready"
                await self.runs.complete(run, processed=count, total=count)
            elif stage is WorkflowStage.ANALYSIS:
                result = await self.analyzer.analyze_batch(
                    task.id,
                    limit=self.analysis_batch_size,
                )
                task.stage = "analysis_ready" if result.remaining == 0 else "differences_ready"
                await self.runs.complete(
                    run,
                    processed=result.total,
                    total=result.total,
                    succeeded=result.succeeded,
                    manual_review=result.manual_review,
                    failed=result.failed,
                )
            else:
                raise ValueError(f"workflow cannot advance stage: {stage.value}")
        except Exception as error:
            workflow_error = _workflow_error(error)
            await self.runs.fail(run, workflow_error)
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
    retryable = isinstance(error, (ConnectionError, TimeoutError))
    return WorkflowError(
        code="workflow_timeout" if retryable else "workflow_stage_failed",
        message=str(error) or type(error).__name__,
        retryable=retryable,
    )

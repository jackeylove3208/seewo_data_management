from uuid import uuid4

import pytest

from app.core.security import OperatorContext
from app.models.reconciliation import ReconciliationTask
from app.schemas.analysis_jobs import AnalysisJobStatus
from app.workflow.service import ReconciliationWorkflowService


class ResolverStub:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve_task(self, _task_id):
        self.calls += 1


class DetectorStub:
    def __init__(self) -> None:
        self.calls = 0

    async def detect(self, task_id):
        self.calls += 1
        values = {"difference_ids": (uuid4(), uuid4()), "task_id": task_id}
        return type("DifferenceResult", (), values)()


class AnalyzerStub:
    def __init__(self) -> None:
        self.calls = 0

    async def create_job(self, task_id, *, idempotency_key):
        self.calls += 1
        assert idempotency_key.startswith("workflow-analysis-v3:")
        return type(
            "AnalysisJob",
            (),
            {
                "id": uuid4(),
                "task_id": task_id,
                "status": AnalysisJobStatus.QUEUED.value,
                "total": 2,
                "completed": 0,
                "succeeded": 0,
                "manual_required": 0,
                "failed": 0,
            },
        )()


class TimeoutOnceDetector(DetectorStub):
    async def detect(self, task_id):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("temporary detector timeout")
        values = {"difference_ids": (uuid4(),), "task_id": task_id}
        return type("DifferenceResult", (), values)()


async def create_task(
    session,
    *,
    stage: str = "snapshots",
    tenant_id: str = "school-1",
    workflow_version: str = "legacy-v1",
):
    record = ReconciliationTask(
        id=uuid4(),
        tenant_id=tenant_id,
        scope_id="all",
        snapshot_mode="full",
        entity_types=["teacher"],
        status="ready",
        stage=stage,
        workflow_version=workflow_version,
        idempotency_key=f"workflow-service-{uuid4()}",
        request_hash="hash",
    )
    session.add(record)
    await session.flush()
    return record


@pytest.mark.asyncio
async def test_workflow_advances_matching_differences_and_analysis(session) -> None:
    record = await create_task(session)
    resolver, detector, analyzer = ResolverStub(), DetectorStub(), AnalyzerStub()
    service = ReconciliationWorkflowService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        resolver=resolver,
        detector=detector,
        analyzer=analyzer,
    )

    matching = await service.advance(record.id)
    differences = await service.advance(record.id)
    analysis = await service.advance(record.id)

    assert matching.workflow.stage.value == "differences"
    assert differences.workflow.stage.value == "analysis"
    assert analysis.workflow.stage.value == "analysis"
    assert analysis.workflow.status.value == "running"
    assert analysis.workflow.analysis.job_id is not None
    assert analysis.workflow.analysis.completed == 0
    assert (resolver.calls, detector.calls, analyzer.calls) == (1, 1, 1)


@pytest.mark.asyncio
async def test_workflow_resumes_from_first_incomplete_stage(session) -> None:
    record = await create_task(session, stage="matching")
    resolver, detector, analyzer = ResolverStub(), DetectorStub(), AnalyzerStub()
    service = ReconciliationWorkflowService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        resolver=resolver,
        detector=detector,
        analyzer=analyzer,
    )

    result = await service.advance(record.id)

    assert result.workflow.stage.value == "analysis"
    assert (resolver.calls, detector.calls, analyzer.calls) == (0, 1, 0)


@pytest.mark.asyncio
async def test_workflow_hides_cross_tenant_task(session) -> None:
    record = await create_task(session, tenant_id="other-school")
    service = ReconciliationWorkflowService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        resolver=ResolverStub(),
        detector=DetectorStub(),
        analyzer=AnalyzerStub(),
    )

    with pytest.raises(LookupError, match="not found"):
        await service.advance(record.id)


@pytest.mark.asyncio
async def test_workflow_retries_only_retryable_failure(session) -> None:
    record = await create_task(session, stage="matching")
    detector = TimeoutOnceDetector()
    service = ReconciliationWorkflowService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        resolver=ResolverStub(),
        detector=detector,
        analyzer=AnalyzerStub(),
    )

    failed = await service.advance(record.id)
    retried = await service.retry(record.id)

    assert failed.workflow.can_retry is True
    assert retried.workflow.stage.value == "analysis"
    attempts = await service.runs.list_attempts(record.id, failed.workflow.stage)
    assert [attempt.attempt for attempt in attempts] == [1, 2]


@pytest.mark.asyncio
async def test_workflow_rejects_unknown_task_stage(session) -> None:
    record = await create_task(session, stage="unknown-stage")
    service = ReconciliationWorkflowService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        resolver=ResolverStub(),
        detector=DetectorStub(),
        analyzer=AnalyzerStub(),
    )

    with pytest.raises(ValueError, match="cannot advance task stage"):
        await service.advance(record.id)


@pytest.mark.asyncio
async def test_legacy_workflow_never_invokes_old_stages_for_agent_task(session) -> None:
    record = await create_task(session, workflow_version="new-agent-v1")
    resolver, detector, analyzer = ResolverStub(), DetectorStub(), AnalyzerStub()
    service = ReconciliationWorkflowService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        resolver=resolver,
        detector=detector,
        analyzer=analyzer,
    )

    with pytest.raises(ValueError, match="legacy workflow cannot process"):
        await service.advance(record.id)

    assert (resolver.calls, detector.calls, analyzer.calls) == (0, 0, 0)

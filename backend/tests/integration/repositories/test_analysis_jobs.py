from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.ai.job_service import AnalysisJobService
from app.core.security import OperatorContext
from app.models.analysis_jobs import AnalysisWorkItemRecord
from app.models.reconciliation import ReconciliationTask
from app.repositories.analysis_jobs import AnalysisJobRepository
from app.repositories.workflow import WorkflowRunRepository
from app.schemas.analysis_jobs import AnalysisJobStatus, AnalysisWorkItemStatus
from app.schemas.differences import DifferenceType
from app.schemas.workflow import WorkflowStage, WorkflowStatus
from tests.integration.ai.test_analysis_service import seed_difference


@pytest.mark.asyncio
async def test_repository_creates_idempotent_job_and_work_items(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    repository = AnalysisJobRepository(session)

    first = await repository.create_or_get(
        task_id=difference.task_id,
        tenant_id="school-1",
        requested_by="operator-1",
        idempotency_key="analysis-request-1",
        difference_versions=((difference.id, difference.version),),
    )
    second = await repository.create_or_get(
        task_id=difference.task_id,
        tenant_id="school-1",
        requested_by="operator-1",
        idempotency_key="analysis-request-1",
        difference_versions=((difference.id, difference.version),),
    )

    assert first.id == second.id
    assert first.total == 1
    assert first.status == AnalysisJobStatus.QUEUED.value


@pytest.mark.asyncio
async def test_repository_claims_one_item_and_updates_counters(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    repository = AnalysisJobRepository(session)
    job = await repository.create_or_get(
        task_id=difference.task_id,
        tenant_id="school-1",
        requested_by="operator-1",
        idempotency_key="analysis-request-2",
        difference_versions=((difference.id, difference.version),),
    )

    item = await repository.claim_next(job.id, worker_id="worker-1", lease_seconds=60)
    assert item is not None
    assert item.status == AnalysisWorkItemStatus.RUNNING.value
    assert item.lease_owner == "worker-1"
    await repository.complete_item(
        item.id,
        worker_id="worker-1",
        outcome=AnalysisWorkItemStatus.SUCCEEDED,
    )
    refreshed = await repository.get(job.id)

    assert refreshed is not None
    assert refreshed.completed == 1
    assert refreshed.proposal_ready == 1
    assert refreshed.status == AnalysisJobStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    repository = AnalysisJobRepository(session)
    job = await repository.create_or_get(
        task_id=difference.task_id,
        tenant_id="school-1",
        requested_by="operator-1",
        idempotency_key="analysis-request-3",
        difference_versions=((difference.id, difference.version),),
    )
    item = await repository.claim_next(job.id, worker_id="worker-1", lease_seconds=-1)
    assert item is not None

    reclaimed = await repository.claim_next(job.id, worker_id="worker-2", lease_seconds=60)

    assert reclaimed is not None
    assert reclaimed.id == item.id
    assert reclaimed.lease_owner == "worker-2"


@pytest.mark.asyncio
async def test_completing_in_flight_item_does_not_revive_canceled_job(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    repository = AnalysisJobRepository(session)
    job = await repository.create_or_get(
        task_id=difference.task_id,
        tenant_id="school-1",
        requested_by="operator-1",
        idempotency_key="analysis-request-cancel",
        difference_versions=((difference.id, difference.version),),
    )
    item = await repository.claim_next(job.id, worker_id="worker-1", lease_seconds=60)
    assert item is not None
    await repository.cancel(job.id)

    await repository.complete_item(
        item.id,
        worker_id="worker-1",
        outcome=AnalysisWorkItemStatus.SUCCEEDED,
    )
    refreshed = await repository.get(job.id)

    assert refreshed is not None
    assert refreshed.status == AnalysisJobStatus.CANCELED.value


@pytest.mark.asyncio
async def test_retry_resumes_a_canceled_job_without_resetting_committed_results(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    repository = AnalysisJobRepository(session)
    job = await repository.create_or_get(
        task_id=difference.task_id,
        tenant_id="school-1",
        requested_by="operator-1",
        idempotency_key="analysis-request-resume",
        difference_versions=((difference.id, difference.version),),
    )
    await repository.cancel(job.id)

    resumed = await repository.retry_failed(job.id)

    assert resumed is not None
    assert resumed.status == AnalysisJobStatus.QUEUED.value
    assert resumed.cancel_requested is False


@pytest.mark.asyncio
async def test_retry_requeues_canceled_work_items(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    repository = AnalysisJobRepository(session)
    job = await repository.create_or_get(
        task_id=difference.task_id,
        tenant_id="school-1",
        requested_by="operator-1",
        idempotency_key="analysis-request-resume-canceled-item",
        difference_versions=((difference.id, difference.version),),
    )
    item = await repository.claim_next(job.id, worker_id="worker-1", lease_seconds=60)
    assert item is not None
    await repository.cancel(job.id)
    await repository.complete_item(
        item.id,
        worker_id="worker-1",
        outcome=AnalysisWorkItemStatus.CANCELED,
    )

    resumed = await repository.retry_failed(job.id)
    reclaimed = await repository.claim_next(job.id, worker_id="worker-2", lease_seconds=60)

    assert resumed is not None
    assert reclaimed is not None
    assert reclaimed.id == item.id


@pytest.mark.asyncio
async def test_cancel_and_retry_update_linked_workflow_projection(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    service = AnalysisJobService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
    )
    job = await service.create_job(
        difference.task_id,
        idempotency_key="analysis-workflow-control",
    )
    task = await session.get(ReconciliationTask, difference.task_id)
    assert task is not None
    task.status = "processing"
    run = await WorkflowRunRepository(session).start(task.id, WorkflowStage.ANALYSIS)
    run.analysis_job_id = job.id
    await session.flush()

    await service.cancel(job.id)

    assert task.status == "failed"
    assert run.status == WorkflowStatus.FAILED.value
    assert run.error is not None
    assert run.error["code"] == "analysis_job_canceled"

    await service.retry(job.id)

    assert task.status == "processing"
    assert task.error is None
    assert run.status == WorkflowStatus.RUNNING.value
    assert run.error is None
    assert run.completed_at is None


@pytest.mark.asyncio
async def test_expired_owner_cannot_heartbeat_or_complete_after_reclaim(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    repository = AnalysisJobRepository(session)
    job = await repository.create_or_get(
        task_id=difference.task_id,
        tenant_id="school-1",
        requested_by="operator-1",
        idempotency_key="analysis-expired-owner",
        difference_versions=((difference.id, difference.version),),
    )
    item = await repository.claim_next(job.id, worker_id="worker-old", lease_seconds=-1)
    assert item is not None

    assert await repository.heartbeat(item.id, worker_id="worker-old", lease_seconds=60) is False
    reclaimed = await repository.claim_next(job.id, worker_id="worker-new", lease_seconds=60)
    assert reclaimed is not None
    with pytest.raises(ValueError, match="lease"):
        await repository.complete_item(
            item.id,
            worker_id="worker-old",
            outcome=AnalysisWorkItemStatus.SUCCEEDED,
        )


@pytest.mark.asyncio
async def test_repository_recovers_expired_leases_for_future_claims(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    repository = AnalysisJobRepository(session)
    job = await repository.create_or_get(
        task_id=difference.task_id,
        tenant_id="school-1",
        requested_by="operator-1",
        idempotency_key="analysis-recover-expired",
        difference_versions=((difference.id, difference.version),),
    )
    item = await repository.claim_next(job.id, worker_id="worker-old", lease_seconds=-1)
    assert item is not None

    recovered = await repository.recover_expired_leases(now=datetime.now(UTC))
    refreshed_item = await session.get(AnalysisWorkItemRecord, item.id)

    assert recovered == 1
    assert refreshed_item is not None
    assert refreshed_item.status == AnalysisWorkItemStatus.QUEUED.value
    assert refreshed_item.lease_owner is None


@pytest.mark.asyncio
async def test_repository_reconciles_job_counters_from_work_items(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    repository = AnalysisJobRepository(session)
    job = await repository.create_or_get(
        task_id=difference.task_id,
        tenant_id="school-1",
        requested_by="operator-1",
        idempotency_key="analysis-reconcile-counters",
        difference_versions=((difference.id, difference.version),),
    )
    item = await repository.claim_next(job.id, worker_id="worker-1", lease_seconds=60)
    assert item is not None
    await repository.complete_item(
        item.id,
        worker_id="worker-1",
        outcome=AnalysisWorkItemStatus.SUCCEEDED,
    )
    job.completed = 0
    job.succeeded = 0
    job.proposal_ready = 0
    job.status = AnalysisJobStatus.RUNNING.value
    job.completed_at = None
    await session.flush()

    reconciled = await repository.reconcile_counters(job.id)

    assert reconciled is not None
    assert reconciled.completed == 1
    assert reconciled.succeeded == 1
    assert reconciled.proposal_ready == 1
    assert reconciled.status == AnalysisJobStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_counter_reconciliation_locks_items_before_job(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    repository = AnalysisJobRepository(session)
    job = await repository.create_or_get(
        task_id=difference.task_id,
        tenant_id="school-1",
        requested_by="operator-1",
        idempotency_key="analysis-reconcile-lock-order",
        difference_versions=((difference.id, difference.version),),
    )
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "analysis_jobs" in statement or "analysis_work_items" in statement:
            statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        await repository.reconcile_counters(job.id)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    item_select = next(
        index
        for index, statement in enumerate(statements)
        if "FROM analysis_work_items" in statement
    )
    job_select = next(
        index for index, statement in enumerate(statements) if "FROM analysis_jobs" in statement
    )
    assert item_select < job_select

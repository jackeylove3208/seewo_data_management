from uuid import uuid4

import pytest

from app.models.reconciliation import ReconciliationTask
from app.repositories.workflow import WorkflowRunRepository
from app.schemas.workflow import WorkflowError, WorkflowStage, WorkflowStatus


async def task(session, *, stage: str = "snapshots") -> ReconciliationTask:
    record = ReconciliationTask(
        id=uuid4(),
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["teacher"],
        status="ready",
        stage=stage,
        idempotency_key=f"workflow-{uuid4()}",
        request_hash="hash",
    )
    session.add(record)
    await session.flush()
    return record


@pytest.mark.asyncio
async def test_workflow_attempts_are_append_only(session) -> None:
    record = await task(session)
    repository = WorkflowRunRepository(session)

    first = await repository.start(record.id, WorkflowStage.MATCHING, total=10)
    await repository.fail(
        first,
        WorkflowError(code="provider_timeout", message="gateway timed out", retryable=True),
    )
    second = await repository.start(record.id, WorkflowStage.MATCHING, total=10)
    await repository.complete(second, processed=10, total=10)

    attempts = await repository.list_attempts(record.id, WorkflowStage.MATCHING)
    assert [attempt.attempt for attempt in attempts] == [1, 2]
    assert attempts[0].status == WorkflowStatus.FAILED.value
    assert attempts[1].status == WorkflowStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_workflow_state_reports_retryable_failure(session) -> None:
    record = await task(session)
    repository = WorkflowRunRepository(session)
    run = await repository.start(record.id, WorkflowStage.MATCHING)
    await repository.fail(
        run,
        WorkflowError(code="provider_timeout", message="gateway timed out", retryable=True),
    )
    record.status = "failed"

    state = await repository.state(record)

    assert state.stage is WorkflowStage.MATCHING
    assert state.status is WorkflowStatus.FAILED
    assert state.error is not None and state.error.code == "provider_timeout"
    assert state.can_retry is True

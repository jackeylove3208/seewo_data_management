from datetime import UTC, datetime, timedelta

import pytest

from app.agent_runtime.repository import (
    AgentRuntimeRepository,
    SchoolLockConflict,
)
from app.agent_runtime.state_machine import AgentPhase, AgentRunKind, AgentRunStatus
from app.models.reconciliation import ReconciliationTask


async def create_task(session, *, tenant_id: str, key: str) -> ReconciliationTask:
    task = ReconciliationTask(
        tenant_id=tenant_id,
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="created",
        stage="ingestion",
        workflow_version="new-agent-v1",
        idempotency_key=key,
        request_hash=key,
    )
    session.add(task)
    await session.flush()
    return task


@pytest.mark.asyncio
async def test_runtime_persists_conversation_run_events_checkpoint_and_safe_failure(
    session,
) -> None:
    task = await create_task(session, tenant_id="school-1", key="agent-runtime-1")
    repository = AgentRuntimeRepository(session)
    conversation = await repository.create_conversation(
        tenant_id="school-1",
        created_by="demo-operator",
    )
    run = await repository.create_run(
        task_id=task.id,
        tenant_id="school-1",
        conversation_id=conversation.id,
        kind=AgentRunKind.SYNC,
    )

    first = await repository.append_event(run.id, "run.created", {"phase": run.phase})
    second = await repository.append_event(run.id, "phase.started", {"safe": True})
    resumed_events = await repository.list_events(run.id, after_sequence=1)
    checkpoint = await repository.save_checkpoint(
        run.id,
        phase=AgentPhase.INGEST_AND_NORMALIZE,
        checkpoint_key="page:1",
        input_hash="a" * 64,
        payload={"cursor": 1},
    )
    replay = await repository.save_checkpoint(
        run.id,
        phase=AgentPhase.INGEST_AND_NORMALIZE,
        checkpoint_key="page:1",
        input_hash="a" * 64,
        payload={"cursor": 1},
    )
    failure = await repository.record_failure(
        run.id,
        phase=AgentPhase.ANALYZE_BATCHES,
        code="agent_model_retries_exhausted",
        safe_message="Agent model processing failed after 4 attempts",
        attempt_count=4,
    )

    assert (first.sequence, second.sequence) == (1, 2)
    assert [event.id for event in resumed_events] == [second.id]
    assert replay.id == checkpoint.id
    assert failure.safe_message == "Agent model processing failed after 4 attempts"
    assert failure.details == {}


@pytest.mark.asyncio
async def test_school_lock_is_exclusive_and_can_be_reacquired_after_audited_release(
    session,
) -> None:
    first_task = await create_task(session, tenant_id="school-1", key="agent-lock-1")
    second_task = await create_task(session, tenant_id="school-1", key="agent-lock-2")
    repository = AgentRuntimeRepository(session)
    first_run = await repository.create_run(
        task_id=first_task.id,
        tenant_id="school-1",
        conversation_id=None,
        kind=AgentRunKind.SYNC,
    )
    second_run = await repository.create_run(
        task_id=second_task.id,
        tenant_id="school-1",
        conversation_id=None,
        kind=AgentRunKind.SYNC,
    )

    first_lock = await repository.acquire_school_lock(
        tenant_id="school-1",
        task_id=first_task.id,
        run_id=first_run.id,
    )
    same_lock = await repository.acquire_school_lock(
        tenant_id="school-1",
        task_id=first_task.id,
        run_id=first_run.id,
    )
    assert same_lock.id == first_lock.id

    with pytest.raises(SchoolLockConflict) as captured:
        await repository.acquire_school_lock(
            tenant_id="school-1",
            task_id=second_task.id,
            run_id=second_run.id,
        )
    assert captured.value.owner_task_id == first_task.id

    released = await repository.release_school_lock(
        tenant_id="school-1",
        run_id=first_run.id,
        reason="report_completed",
    )
    assert released.active is False
    assert released.release_reason == "report_completed"

    second_lock = await repository.acquire_school_lock(
        tenant_id="school-1",
        task_id=second_task.id,
        run_id=second_run.id,
    )
    assert second_lock.active is True


@pytest.mark.asyncio
async def test_transition_rejects_skipping_and_persists_legal_progress(session) -> None:
    task = await create_task(session, tenant_id="school-1", key="agent-transition-1")
    repository = AgentRuntimeRepository(session)
    run = await repository.create_run(
        task_id=task.id,
        tenant_id="school-1",
        conversation_id=None,
        kind=AgentRunKind.SYNC,
    )

    advanced = await repository.transition_run(
        run.id,
        requested_phase=AgentPhase.ACQUIRE_SCHOOL_LOCK,
    )

    assert advanced.phase == AgentPhase.ACQUIRE_SCHOOL_LOCK.value
    assert advanced.status == AgentRunStatus.RUNNING.value
    assert advanced.version == 2


@pytest.mark.asyncio
async def test_expired_run_lease_can_be_reclaimed_without_losing_attempt_history(session) -> None:
    task = await create_task(session, tenant_id="school-1", key="agent-lease-1")
    repository = AgentRuntimeRepository(session)
    run = await repository.create_run(
        task_id=task.id,
        tenant_id="school-1",
        conversation_id=None,
        kind=AgentRunKind.SYNC,
    )
    run = await repository.transition_run(
        run.id, requested_phase=AgentPhase.ACQUIRE_SCHOOL_LOCK
    )
    run = await repository.transition_run(
        run.id, requested_phase=AgentPhase.INGEST_AND_NORMALIZE
    )

    claimed = await repository.claim_next_run(
        worker_id="worker-1",
        lease_seconds=60,
        phases=frozenset({AgentPhase.INGEST_AND_NORMALIZE}),
    )
    assert claimed is not None
    assert claimed.attempt_count == 1
    assert claimed.lease_token is not None
    first_lease_token = claimed.lease_token
    assert (
        await repository.claim_next_run(
            worker_id="worker-2",
            lease_seconds=60,
            phases=frozenset({AgentPhase.INGEST_AND_NORMALIZE}),
        )
        is None
    )

    claimed.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    assert (
        await repository.heartbeat_run_claim(
            run.id,
            worker_id="worker-1",
            lease_token=first_lease_token,
            lease_seconds=60,
        )
        is False
    )
    reclaimed = await repository.claim_next_run(
        worker_id="worker-2",
        lease_seconds=60,
        phases=frozenset({AgentPhase.INGEST_AND_NORMALIZE}),
    )
    assert reclaimed is not None
    assert reclaimed.id == run.id
    assert reclaimed.lease_owner == "worker-2"
    assert reclaimed.attempt_count == 2
    assert reclaimed.lease_token is not None
    assert reclaimed.lease_token != first_lease_token
    assert (
        await repository.heartbeat_run_claim(
            run.id,
            worker_id="worker-1",
            lease_token=first_lease_token,
            lease_seconds=60,
        )
        is False
    )

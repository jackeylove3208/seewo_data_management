import asyncio

import pytest
from sqlalchemy import select

from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.service import AgentSupervisorService
from app.agent_runtime.state_machine import AgentPhase
from app.agent_runtime.worker import AgentLeaseLost, AgentWorker, AgentWorkResult
from app.core.security import OperatorContext
from app.models.agent_runtime import SchoolTaskLockRecord
from app.models.reconciliation import ReconciliationTask


def supervisor(session) -> AgentSupervisorService:
    return AgentSupervisorService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
    )


@pytest.mark.asyncio
async def test_worker_claims_one_phase_and_commits_the_handler_transition(database) -> None:
    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="new-agent-v1",
            idempotency_key="worker-task",
            request_hash="worker-task",
        )
        session.add(task)
        await session.flush()
        run = await supervisor(session).start(
            task_id=task.id,
            conversation_id=None,
        )
        run_id = run.id
        await session.commit()

    calls = []

    async def ingest_handler(context):
        calls.append(context.run_id)
        return AgentWorkResult(next_phase=AgentPhase.BUILD_IDENTITY_WORK)

    worker = AgentWorker(
        database.session_factory,
        worker_id="agent-worker-1",
        lease_seconds=60,
        handlers={AgentPhase.INGEST_AND_NORMALIZE: ingest_handler},
    )

    assert await worker.run_once() is True
    assert calls == [run_id]
    async with database.session_factory() as session:
        persisted = await AgentRuntimeRepository(session).get_run(run_id)
        assert persisted is not None
        assert persisted.phase == AgentPhase.BUILD_IDENTITY_WORK.value
        assert persisted.lease_owner is None


@pytest.mark.asyncio
async def test_worker_renews_run_lease_and_school_lock_during_long_handler(database) -> None:
    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="new-agent-v1",
            idempotency_key="worker-heartbeat-task",
            request_hash="worker-heartbeat-task",
        )
        session.add(task)
        await session.flush()
        run = await supervisor(session).start(
            task_id=task.id,
            conversation_id=None,
        )
        run_id = run.id
        initial_run_heartbeat = run.heartbeat_at
        lock = await session.scalar(
            select(SchoolTaskLockRecord).where(SchoolTaskLockRecord.owner_run_id == run_id)
        )
        assert lock is not None
        initial_lock_heartbeat = lock.heartbeat_at
        await session.commit()

    handler_started = asyncio.Event()
    allow_handler_to_finish = asyncio.Event()

    async def slow_handler(_context):
        handler_started.set()
        await allow_handler_to_finish.wait()
        return AgentWorkResult(next_phase=AgentPhase.BUILD_IDENTITY_WORK)

    worker = AgentWorker(
        database.session_factory,
        worker_id="agent-worker-heartbeat",
        lease_seconds=1,
        heartbeat_interval_seconds=0.01,
        handlers={AgentPhase.INGEST_AND_NORMALIZE: slow_handler},
    )
    worker_task = asyncio.create_task(worker.run_once())
    renewed = False
    try:
        await handler_started.wait()
        for _ in range(50):
            await asyncio.sleep(0.01)
            async with database.session_factory() as session:
                persisted = await AgentRuntimeRepository(session).get_run(run_id)
                lock = await session.scalar(
                    select(SchoolTaskLockRecord).where(
                        SchoolTaskLockRecord.owner_run_id == run_id
                    )
                )
                assert persisted is not None
                assert lock is not None
                renewed = (
                    persisted.heartbeat_at is not None
                    and persisted.heartbeat_at != initial_run_heartbeat
                    and lock.heartbeat_at != initial_lock_heartbeat
                    and persisted.lease_expires_at is not None
                    and persisted.lease_expires_at > persisted.heartbeat_at
                )
            if renewed:
                break
    finally:
        allow_handler_to_finish.set()

    assert await worker_task is True
    assert renewed is True


@pytest.mark.asyncio
async def test_worker_cancels_handler_when_lease_ownership_is_lost(database) -> None:
    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="new-agent-v1",
            idempotency_key="worker-fencing-task",
            request_hash="worker-fencing-task",
        )
        session.add(task)
        await session.flush()
        run = await supervisor(session).start(
            task_id=task.id,
            conversation_id=None,
        )
        run_id = run.id
        await session.commit()

    handler_started = asyncio.Event()
    handler_cancelled = asyncio.Event()

    async def fenced_handler(_context):
        handler_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            handler_cancelled.set()

    worker = AgentWorker(
        database.session_factory,
        worker_id="agent-worker-fenced",
        lease_seconds=1,
        heartbeat_interval_seconds=0.01,
        handlers={AgentPhase.INGEST_AND_NORMALIZE: fenced_handler},
    )
    worker_task = asyncio.create_task(worker.run_once())
    await handler_started.wait()
    async with database.session_factory() as session:
        async with session.begin():
            persisted = await AgentRuntimeRepository(session).get_run(
                run_id, for_update=True
            )
            assert persisted is not None
            persisted.lease_owner = "new-owner"

    with pytest.raises(AgentLeaseLost):
        await worker_task
    assert handler_cancelled.is_set()

    async with database.session_factory() as session:
        persisted = await AgentRuntimeRepository(session).get_run(run_id)
        assert persisted is not None
        assert persisted.phase == AgentPhase.INGEST_AND_NORMALIZE.value


@pytest.mark.asyncio
async def test_worker_cancels_handler_when_heartbeat_raises(database, monkeypatch) -> None:
    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="new-agent-v1",
            idempotency_key="worker-heartbeat-error",
            request_hash="worker-heartbeat-error",
        )
        session.add(task)
        await session.flush()
        await supervisor(session).start(task_id=task.id, conversation_id=None)
        await session.commit()

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_context):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def broken_heartbeat(*_args, **_kwargs):
        raise RuntimeError("heartbeat-db-down")

    monkeypatch.setattr(
        AgentRuntimeRepository, "heartbeat_run_claim", broken_heartbeat
    )
    worker = AgentWorker(
        database.session_factory,
        worker_id="agent-worker-heartbeat-error",
        lease_seconds=1,
        heartbeat_interval_seconds=0.01,
        handlers={AgentPhase.INGEST_AND_NORMALIZE: handler},
    )
    work = asyncio.create_task(worker.run_once())
    await started.wait()

    with pytest.raises(RuntimeError, match="heartbeat-db-down"):
        await work
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_cancelling_worker_cancels_handler_and_releases_claim(database) -> None:
    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="new-agent-v1",
            idempotency_key="worker-parent-cancel",
            request_hash="worker-parent-cancel",
        )
        session.add(task)
        await session.flush()
        run = await supervisor(session).start(task_id=task.id, conversation_id=None)
        run_id = run.id
        await session.commit()

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_context):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    worker = AgentWorker(
        database.session_factory,
        worker_id="agent-worker-parent-cancel",
        lease_seconds=60,
        handlers={AgentPhase.INGEST_AND_NORMALIZE: handler},
    )
    work = asyncio.create_task(worker.run_once())
    await started.wait()
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work

    assert cancelled.is_set()
    async with database.session_factory() as session:
        persisted = await AgentRuntimeRepository(session).get_run(run_id)
        assert persisted is not None
        assert persisted.lease_owner is None
        assert persisted.lease_token is None

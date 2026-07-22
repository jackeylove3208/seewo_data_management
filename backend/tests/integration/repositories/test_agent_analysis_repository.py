from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.models.agent_analysis import (
    AgentIdentityPostingRecord,
    AgentModelAttemptRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
    ImmutableAgentAnalysisRecordError,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.agent_analysis import AgentAnalysisRepository, ReplayConflict
from app.schemas.agent_ingestion import AgentContractRecord, AgentEntityKind, AgentSourceRole
from app.schemas.agent_reconciliation import AgentFindingPayload, AgentSolutionPayload


async def _run_context(session):
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        workflow_version="new-agent-v1",
        idempotency_key=f"agent-analysis-{uuid4()}",
        request_hash="a" * 64,
    )
    session.add(task)
    await session.flush()
    run = await AgentRuntimeRepository(session).create_run(
        task_id=task.id,
        tenant_id=task.tenant_id,
        conversation_id=None,
        kind=AgentRunKind.SYNC,
    )
    snapshots = []
    for role in ("authoritative", "target"):
        source_file = SourceFile(
            task_id=task.id,
            source_role=role,
            original_name=f"{role}.csv",
            storage_name=f"{uuid4()}.csv",
            storage_path=f"/synthetic/{uuid4()}.csv",
            sha256=uuid4().hex * 2,
            size_bytes=1,
        )
        session.add(source_file)
        await session.flush()
        snapshot = Snapshot(
            id=uuid4(),
            task_id=task.id,
            source_file_id=source_file.id,
            source_role=role,
            schema_version="agent-contract-v1",
            mapping_version="agent-contract-v1",
            file_hash=source_file.sha256,
            content_hash=uuid4().hex * 2,
            summary={},
        )
        session.add(snapshot)
        snapshots.append(snapshot)
    await session.flush()
    return task, run, snapshots[0], snapshots[1]


async def _lease_run(session, run: AgentRunRecord, worker_id: str = "worker-1"):
    run.status = "running"
    run.lease_owner = worker_id
    run.lease_token = uuid4()
    run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    await session.flush()
    return run.lease_token


def _record(task_id, run_id, **changes):
    values = {
        "task_id": task_id,
        "run_id": run_id,
        "snapshot_id": uuid4(),
        "tenant_id": "school-1",
        "source_role": AgentSourceRole.AUTHORITATIVE,
        "stable_locator": "csv:authority:2",
        "stable_order": 2,
        "entity_kind": AgentEntityKind.STUDENT,
        "category": "学生",
        "name": "测试学生",
        "number": "S-001",
        "class_name": "一年级一班",
        "phone": "13800000000",
        "email": "student@example.test",
    }
    values.update(changes)
    return AgentContractRecord.model_validate(values)


@pytest.mark.asyncio
async def test_input_replay_is_idempotent_and_mismatched_locator_fails_closed(session) -> None:
    task, run, authority_snapshot, _ = await _run_context(session)
    repository = AgentAnalysisRepository(session)
    record = _record(task.id, run.id, snapshot_id=authority_snapshot.id)

    first = (await repository.persist_inputs((record,)))[0]
    replay = (await repository.persist_inputs((record,)))[0]

    assert replay.id == first.id
    with pytest.raises(ReplayConflict, match="stable locator"):
        await repository.persist_inputs(
            (_record(task.id, run.id, snapshot_id=authority_snapshot.id, name="另一位学生"),)
        )


@pytest.mark.asyncio
async def test_input_persistence_rejects_cross_tenant_run_and_wrong_snapshot(session) -> None:
    task, run, authority_snapshot, target_snapshot = await _run_context(session)
    repository = AgentAnalysisRepository(session)

    with pytest.raises(ReplayConflict, match="tenant"):
        await repository.persist_inputs(
            (_record(task.id, run.id, snapshot_id=authority_snapshot.id, tenant_id="school-2"),)
        )
    with pytest.raises(ReplayConflict, match="source role"):
        await repository.persist_inputs((_record(task.id, run.id, snapshot_id=target_snapshot.id),))


@pytest.mark.asyncio
async def test_append_only_input_rows_cannot_be_updated(session) -> None:
    task, run, authority_snapshot, _ = await _run_context(session)
    input_record = (
        await AgentAnalysisRepository(session).persist_inputs(
            (_record(task.id, run.id, snapshot_id=authority_snapshot.id),)
        )
    )[0]

    input_record.name = "被修改"
    with pytest.raises(ImmutableAgentAnalysisRecordError):
        await session.flush()


@pytest.mark.asyncio
async def test_identity_postings_allow_duplicate_values_but_forbid_replay_of_same_row(
    session,
) -> None:
    task, run, authority_snapshot, _ = await _run_context(session)
    repository = AgentAnalysisRepository(session)
    first, second = await repository.persist_inputs(
        (
            _record(task.id, run.id, snapshot_id=authority_snapshot.id),
            _record(
                task.id,
                run.id,
                snapshot_id=authority_snapshot.id,
                stable_locator="csv:authority:3",
                stable_order=3,
            ),
        )
    )

    await repository.persist_identity_postings(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        snapshot_id=first.snapshot_id,
        postings=((first.id, "number", "S-001"), (second.id, "number", "S-001")),
    )
    rows = tuple(await session.scalars(select(AgentIdentityPostingRecord)))
    assert len(rows) == 2
    with pytest.raises(IntegrityError):
        session.add(
            AgentIdentityPostingRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                snapshot_id=first.snapshot_id,
                input_record_id=first.id,
                entity_kind="student",
                key_kind="number",
                normalized_value="S-001",
            )
        )
        await session.flush()


@pytest.mark.asyncio
async def test_batch_claim_and_atomic_finalization_enforce_fencing_and_attempt_limits(
    session,
) -> None:
    task, run, authority_snapshot, target_snapshot = await _run_context(session)
    repository = AgentAnalysisRepository(session)
    inputs = await repository.persist_inputs(
        tuple(
            _record(
                task.id,
                run.id,
                snapshot_id=target_snapshot.id,
                source_role=AgentSourceRole.TARGET,
                stable_locator=f"csv:target:{index}",
                stable_order=index,
            )
            for index in (2, 3)
        )
    )
    work_items = tuple(
        AgentWorkItemRecord(
            run_id=run.id,
            task_id=task.id,
            tenant_id=task.tenant_id,
            source_snapshot_id=authority_snapshot.id,
            target_snapshot_id=target_snapshot.id,
            subject_input_id=input_record.id,
            entity_kind="student",
            kind="target_extra",
            state="pending",
            idempotency_hash=uuid4().hex * 2,
            evidence_hash=uuid4().hex * 2,
        )
        for input_record in inputs
    )
    session.add_all(work_items)
    await session.flush()
    item_ids = tuple(item.id for item in work_items)
    batch = await repository.create_or_get_batch(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        entity_kind="student",
        input_hash="b" * 64,
        work_item_ids=item_ids,
    )
    assert (
        batch.id
        == (
            await repository.create_or_get_batch(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                entity_kind="student",
                input_hash="b" * 64,
                work_item_ids=item_ids,
            )
        ).id
    )
    run_lease_token = await _lease_run(session, run)
    claim = await repository.claim_batch(
        batch.id,
        worker_id="worker-1",
        run_lease_token=run_lease_token,
        lease_seconds=60,
    )
    assert claim is not None and claim.lease_token is not None
    finding = AgentFindingPayload(
        work_item_id=item_ids[0],
        kind="target_extra",
        category_zh="多余记录",
        analysis_zh="没有权威候选。",
        evidence_refs=("evidence:1",),
        solutions=(AgentSolutionPayload(operation="delete", risk="high", solution_zh="删除"),),
    )
    await repository.finalize_batch(
        batch_id=batch.id,
        worker_id="worker-1",
        lease_token=claim.lease_token,
        run_lease_token=run_lease_token,
        output_hash="c" * 64,
        findings=(finding,),
    )
    completed = await session.get(AgentModelBatchRecord, batch.id)
    assert completed is not None and completed.status == "completed"
    assert len(tuple(await session.scalars(select(AgentModelAttemptRecord)))) == 1
    assert (
        await repository.claim_batch(
            batch.id,
            worker_id="worker-2",
            run_lease_token=uuid4(),
            lease_seconds=60,
        )
        is None
    )


@pytest.mark.asyncio
async def test_expired_run_lease_cannot_write_attempts_or_findings(session) -> None:
    task, run, authority_snapshot, target_snapshot = await _run_context(session)
    repository = AgentAnalysisRepository(session)
    target = (
        await repository.persist_inputs(
            (
                _record(
                    task.id,
                    run.id,
                    snapshot_id=target_snapshot.id,
                    source_role=AgentSourceRole.TARGET,
                ),
            )
        )
    )[0]
    work_item = AgentWorkItemRecord(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        source_snapshot_id=authority_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        subject_input_id=target.id,
        entity_kind="student",
        kind="target_extra",
        state="pending",
        idempotency_hash="d" * 64,
        evidence_hash="e" * 64,
    )
    session.add(work_item)
    await session.flush()
    batch = await repository.create_or_get_batch(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        entity_kind="student",
        input_hash="f" * 64,
        work_item_ids=(work_item.id,),
    )
    run_token = await _lease_run(session, run)
    claim = await repository.claim_batch(
        batch.id,
        worker_id="worker-1",
        run_lease_token=run_token,
        lease_seconds=60,
    )
    assert claim is not None and claim.lease_token is not None
    run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    with pytest.raises(ReplayConflict, match="run claim"):
        await repository.finalize_batch(
            batch_id=batch.id,
            worker_id="worker-1",
            run_lease_token=run_token,
            lease_token=claim.lease_token,
            output_hash="0" * 64,
            findings=(
                AgentFindingPayload(
                    work_item_id=work_item.id,
                    kind="target_extra",
                    category_zh="多余记录",
                    analysis_zh="没有权威候选。",
                    evidence_refs=("evidence:1",),
                    solutions=(
                        AgentSolutionPayload(operation="delete", risk="high", solution_zh="删除"),
                    ),
                ),
            ),
        )
    assert tuple(await session.scalars(select(AgentModelAttemptRecord))) == ()

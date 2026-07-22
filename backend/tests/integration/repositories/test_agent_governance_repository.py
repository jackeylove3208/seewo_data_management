from uuid import uuid4

import pytest

from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.governance.agent_governance import AgentApprovalGroup
from app.models.agent_analysis import AgentInputRecord, AgentWorkItemRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.agent_governance import AgentGovernanceRepository, GovernanceReplayConflict


async def _context(session):
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        workflow_version="new-agent-v1",
        idempotency_key=uuid4().hex,
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
        source = SourceFile(
            task_id=task.id,
            source_role=role,
            original_name=f"{role}.csv",
            storage_name=uuid4().hex,
            storage_path=f"/synthetic/{uuid4().hex}.csv",
            sha256=uuid4().hex * 2,
            size_bytes=1,
        )
        session.add(source)
        await session.flush()
        snapshot = Snapshot(
            id=uuid4(),
            task_id=task.id,
            source_file_id=source.id,
            source_role=role,
            schema_version="agent-contract-v1",
            mapping_version="agent-contract-v1",
            file_hash=source.sha256,
            content_hash=uuid4().hex * 2,
            summary={},
        )
        session.add(snapshot)
        snapshots.append(snapshot)
    await session.flush()
    return task, run, snapshots


@pytest.mark.asyncio
async def test_approval_membership_is_frozen_and_decision_is_audited(session) -> None:
    task, run, snapshots = await _context(session)
    repository = AgentGovernanceRepository(session)
    group = AgentApprovalGroup(
        id=uuid4(),
        finding_ids=(uuid4(),),
        issue_kind="target_extra",
        entity_kind="student",
        operation="delete",
        policy_version="agent-risk-v1",
        membership_hash="b" * 64,
    )
    saved = await repository.save_approval_group(run=run, task=task, group=group)
    assert saved.id == group.id
    decided = await repository.decide_approval(
        saved.id,
        membership_hash="b" * 64,
        approved=True,
        actor_id="operator-1",
        reason="确认",
    )
    assert decided.status == "approved"
    with pytest.raises(GovernanceReplayConflict, match="stale"):
        await repository.decide_approval(
            saved.id,
            membership_hash="c" * 64,
            approved=True,
            actor_id="operator-1",
            reason="重试",
        )


@pytest.mark.asyncio
async def test_clarification_requires_interpretation_then_second_confirmation(session) -> None:
    task, run, snapshots = await _context(session)
    repository = AgentGovernanceRepository(session)
    input_record = AgentInputRecord(
        id=uuid4(),
        run_id=run.id,
        task_id=task.id,
        snapshot_id=snapshots[1].id,
        tenant_id=task.tenant_id,
        source_role="target",
        stable_locator="row:1",
        stable_order=1,
        entity_kind="student",
        input_hash="c" * 64,
    )
    session.add(input_record)
    await session.flush()
    work_item = AgentWorkItemRecord(
        id=uuid4(),
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        source_snapshot_id=snapshots[0].id,
        target_snapshot_id=snapshots[1].id,
        subject_input_id=input_record.id,
        entity_kind="student",
        kind="identity_conflict",
        state="awaiting_clarification",
        idempotency_hash="d" * 64,
        evidence_hash="e" * 64,
    )
    session.add(work_item)
    await session.flush()
    work_item_id = work_item.id
    clarification = await repository.create_clarification(
        run=run,
        task=task,
        work_item_id=work_item_id,
        candidates=({"id": str(uuid4()), "masked_number": "S-***"},),
        allowed_outcomes=("skip",),
    )
    interpreted = await repository.record_clarification_interpretation(
        clarification.id,
        original_text="skip",
        interpretation={"outcome": "skip"},
        actor_id="operator-1",
    )
    assert interpreted.status == "interpreted"
    confirmed = await repository.confirm_clarification(
        clarification.id,
        actor_id="operator-1",
        confirmed=True,
    )
    assert confirmed.status == "confirmed"

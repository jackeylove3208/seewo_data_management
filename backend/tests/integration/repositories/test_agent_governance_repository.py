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
async def test_approval_groups_with_different_changed_fields_have_distinct_keys(
    session,
) -> None:
    task, run, _snapshots = await _context(session)
    repository = AgentGovernanceRepository(session)
    phone_only = AgentApprovalGroup(
        id=uuid4(),
        finding_ids=(uuid4(),),
        issue_kind="field_difference",
        entity_kind="student",
        operation="update",
        policy_version="agent-risk-v1",
        membership_hash="d" * 64,
        changed_fields=("phone",),
    )
    phone_and_email = AgentApprovalGroup(
        id=uuid4(),
        finding_ids=(uuid4(),),
        issue_kind="field_difference",
        entity_kind="student",
        operation="update",
        policy_version="agent-risk-v1",
        membership_hash="e" * 64,
        changed_fields=("email", "phone"),
    )

    first = await repository.save_approval_group(
        run=run,
        task=task,
        group=phone_only,
    )
    second = await repository.save_approval_group(
        run=run,
        task=task,
        group=phone_and_email,
    )

    assert first.group_key != second.group_key
    assert first.status == second.status == "pending"


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
    replayed = await repository.confirm_clarification(
        clarification.id,
        actor_id="operator-1",
        confirmed=True,
    )
    assert replayed.status == "confirmed"


@pytest.mark.asyncio
async def test_structured_clarification_selection_is_idempotent_and_replaceable(session) -> None:
    task, run, snapshots = await _context(session)
    repository = AgentGovernanceRepository(session)
    input_record = AgentInputRecord(
        id=uuid4(),
        run_id=run.id,
        task_id=task.id,
        snapshot_id=snapshots[1].id,
        tenant_id=task.tenant_id,
        source_role="target",
        stable_locator="row:structured",
        stable_order=3,
        entity_kind="student",
        input_hash="3" * 64,
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
        idempotency_hash="4" * 64,
        evidence_hash="5" * 64,
    )
    session.add(work_item)
    await session.flush()
    first_candidate_id = uuid4()
    second_candidate_id = uuid4()
    clarification = await repository.create_clarification(
        run=run,
        task=task,
        work_item_id=work_item.id,
        candidates=(
            {"id": str(first_candidate_id), "masked_number": "S-***1"},
            {"id": str(second_candidate_id), "masked_number": "S-***2"},
        ),
        allowed_outcomes=("use_candidate", "target_extra"),
    )

    first, first_created = await repository.record_structured_clarification_selection(
        clarification.id,
        tenant_id=task.tenant_id,
        decision="select_candidate",
        selected_candidate_id=first_candidate_id,
        note="采用候选 A",
        interpretation_zh="你选择了第三方候选 A，确认后继续。",
        idempotency_key="selection-1",
        actor_id="operator-1",
    )

    assert first_created is True
    assert first.status == "interpreted"
    assert first.original_text == "采用候选 A"
    assert first.interpretation == {
        "outcome": "use_candidate",
        "candidate_id": str(first_candidate_id),
        "note": "采用候选 A",
        "interpretation_zh": "你选择了第三方候选 A，确认后继续。",
        "model_decision": "select_candidate",
        "submission_source": "structured_selection",
        "idempotency_key": "selection-1",
    }

    replayed, replay_created = (
        await repository.record_structured_clarification_selection(
            clarification.id,
            tenant_id=task.tenant_id,
            decision="select_candidate",
            selected_candidate_id=first_candidate_id,
            note="采用候选 A",
            interpretation_zh="你选择了第三方候选 A，确认后继续。",
            idempotency_key="selection-1",
            actor_id="operator-1",
        )
    )
    assert replay_created is False
    assert replayed.interpretation == first.interpretation

    replaced, replacement_created = (
        await repository.record_structured_clarification_selection(
            clarification.id,
            tenant_id=task.tenant_id,
            decision="select_candidate",
            selected_candidate_id=second_candidate_id,
            note=None,
            interpretation_zh="你选择了第三方候选 B，确认后继续。",
            idempotency_key="selection-2",
            actor_id="operator-1",
        )
    )
    assert replacement_created is True
    assert replaced.original_text == "操作人通过结构化控件提交身份冲突选择"
    assert replaced.interpretation is not None
    assert replaced.interpretation["candidate_id"] == str(second_candidate_id)

    await repository.confirm_clarification(
        clarification.id,
        actor_id="operator-1",
        confirmed=True,
    )
    with pytest.raises(GovernanceReplayConflict, match="not awaiting"):
        await repository.record_structured_clarification_selection(
            clarification.id,
            tenant_id=task.tenant_id,
            decision="treat_as_extra",
            selected_candidate_id=None,
            note=None,
            interpretation_zh="你选择了按希沃多余处理，确认后继续。",
            idempotency_key="selection-3",
            actor_id="operator-1",
        )


@pytest.mark.asyncio
async def test_unresolved_clarification_feedback_survives_page_reload(session) -> None:
    task, run, snapshots = await _context(session)
    repository = AgentGovernanceRepository(session)
    input_record = AgentInputRecord(
        id=uuid4(),
        run_id=run.id,
        task_id=task.id,
        snapshot_id=snapshots[1].id,
        tenant_id=task.tenant_id,
        source_role="target",
        stable_locator="row:2",
        stable_order=2,
        entity_kind="student",
        input_hash="f" * 64,
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
        idempotency_hash="1" * 64,
        evidence_hash="2" * 64,
    )
    session.add(work_item)
    await session.flush()
    clarification = await repository.create_clarification(
        run=run,
        task=task,
        work_item_id=work_item.id,
        candidates=({"id": str(uuid4()), "masked_number": "S-***"},),
        allowed_outcomes=("use_candidate", "target_extra"),
    )

    feedback = await repository.record_clarification_feedback(
        clarification.id,
        original_text="他们可能是同一个人。",
        feedback_zh="当前说明无法唯一确定候选，请明确选择候选 A 或按希沃多余处理。",
        actor_id="operator-1",
    )

    assert feedback.status == "pending"
    assert feedback.original_text == "他们可能是同一个人。"
    assert feedback.interpretation == {
        "outcome": "leave_unresolved",
        "interpretation_zh": "当前说明无法唯一确定候选，请明确选择候选 A 或按希沃多余处理。",
        "model_decision": "leave_unresolved",
    }
    interpreted = await repository.record_clarification_interpretation(
        clarification.id,
        original_text="选择候选 A。",
        interpretation={"outcome": "use_candidate"},
        actor_id="operator-1",
    )
    assert interpreted.status == "interpreted"

    revised_feedback = await repository.record_clarification_feedback(
        clarification.id,
        original_text="还是无法确定。",
        feedback_zh="请明确选择候选 A，或按希沃多余处理。",
        actor_id="operator-1",
    )
    assert revised_feedback.status == "pending"
    assert revised_feedback.original_text == "还是无法确定。"

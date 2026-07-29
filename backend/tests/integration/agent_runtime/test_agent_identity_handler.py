from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.ai.agent_batching import AgentBatchPlanner
from app.models.agent_analysis import (
    AgentClarificationRecord,
    AgentIdentityClaimRecord,
    AgentInputRecord,
    AgentModelBatchItemRecord,
    AgentWorkItemRecord,
)
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.reconciliation.agent_identity import AgentIdentityIndexBuilder
from app.repositories.agent_analysis import AgentAnalysisRepository
from app.repositories.agent_governance import AgentGovernanceRepository
from app.schemas.agent_ingestion import AgentContractRecord, AgentEntityKind, AgentSourceRole


@pytest.mark.asyncio
async def test_builder_marks_correct_rows_silent_and_emits_duplicate_extra_and_missing_work(
    session,
) -> None:
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        workflow_version="new-agent-v1",
        idempotency_key=f"identity-{uuid4()}",
        request_hash="a" * 64,
    )
    session.add(task)
    await session.flush()
    run = await AgentRuntimeRepository(session).create_run(
        task_id=task.id, tenant_id=task.tenant_id, conversation_id=None, kind=AgentRunKind.SYNC,
    )
    snapshots = {}
    for role in ("authoritative", "target"):
        source = SourceFile(
            task_id=task.id,
            source_role=role,
            original_name=f"{role}.csv",
            storage_name=f"{uuid4()}.csv",
            storage_path=f"/synthetic/{uuid4()}.csv",
            sha256=uuid4().hex * 2,
            size_bytes=1,
        )
        session.add(source)
        await session.flush()
        snapshot = Snapshot(
            id=uuid4(), task_id=task.id, source_file_id=source.id, source_role=role,
            schema_version="agent-contract-v1", mapping_version="agent-contract-v1",
            file_hash=source.sha256, content_hash=uuid4().hex * 2, summary={},
        )
        session.add(snapshot)
        snapshots[role] = snapshot
    await session.flush()
    repository = AgentAnalysisRepository(session)
    records = []
    for role, row, number in (
        ("authoritative", 2, "S1"), ("authoritative", 3, "S2"),
        ("target", 2, "S1"), ("target", 3, "S1"), ("target", 4, "S9"),
    ):
        records.append(
            AgentContractRecord(
                task_id=task.id,
                run_id=run.id,
                snapshot_id=snapshots[role].id,
                tenant_id=task.tenant_id,
                source_role=AgentSourceRole(role),
                stable_locator=f"csv:{role}:{row}",
                stable_order=row,
                entity_kind=AgentEntityKind.STUDENT,
                category="student",
                name="李四",
                number=number,
                class_name="一班",
                phone=None,
                email=None,
                raw_row_number=row,
            )
        )
    await repository.persist_inputs(tuple(records))

    await AgentIdentityIndexBuilder(session).build(run_id=run.id)

    work_items = tuple(
        await session.scalars(select(AgentWorkItemRecord).order_by(AgentWorkItemRecord.kind))
    )
    assert [item.kind for item in work_items] == [
        "correct", "target_duplicate", "target_extra", "target_missing"
    ]
    assert len(tuple(await session.scalars(select(AgentInputRecord)))) == 5

    batches = await AgentBatchPlanner(session).create_for_run(run_id=run.id)

    assert [batch.item_count for batch in batches] == [3]


async def _identity_conflict_run(session):
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        workflow_version="agent-graph-v1",
        idempotency_key=f"identity-conflict-{uuid4()}",
        request_hash="b" * 64,
    )
    session.add(task)
    await session.flush()
    run = await AgentRuntimeRepository(session).create_run(
        task_id=task.id,
        tenant_id=task.tenant_id,
        conversation_id=None,
        kind=AgentRunKind.SYNC,
    )
    snapshots = {}
    for role in ("authoritative", "target"):
        source = SourceFile(
            task_id=task.id,
            source_role=role,
            original_name=f"{role}.csv",
            storage_name=f"{uuid4()}.csv",
            storage_path=f"/synthetic/{uuid4()}.csv",
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
        snapshots[role] = snapshot
    await session.flush()

    records = (
        AgentContractRecord(
            task_id=task.id,
            run_id=run.id,
            snapshot_id=snapshots["authoritative"].id,
            tenant_id=task.tenant_id,
            source_role=AgentSourceRole.AUTHORITATIVE,
            stable_locator="csv:authority:2",
            stable_order=1,
            entity_kind=AgentEntityKind.STUDENT,
            category="学生",
            name="候选甲",
            number="S1",
            class_name="一班",
            phone="13800138001",
            email="s1@example.test",
            raw_row_number=2,
        ),
        AgentContractRecord(
            task_id=task.id,
            run_id=run.id,
            snapshot_id=snapshots["authoritative"].id,
            tenant_id=task.tenant_id,
            source_role=AgentSourceRole.AUTHORITATIVE,
            stable_locator="csv:authority:3",
            stable_order=2,
            entity_kind=AgentEntityKind.STUDENT,
            category="学生",
            name="候选乙",
            number="S2",
            class_name="二班",
            phone="13800138002",
            email="s2@example.test",
            raw_row_number=3,
        ),
        AgentContractRecord(
            task_id=task.id,
            run_id=run.id,
            snapshot_id=snapshots["target"].id,
            tenant_id=task.tenant_id,
            source_role=AgentSourceRole.TARGET,
            stable_locator="csv:target:2",
            stable_order=1,
            entity_kind=AgentEntityKind.STUDENT,
            category="学生",
            name="候选甲",
            number="S1",
            class_name="一班",
            phone="13800138002",
            email="s2@example.test",
            raw_row_number=2,
        ),
        AgentContractRecord(
            task_id=task.id,
            run_id=run.id,
            snapshot_id=snapshots["target"].id,
            tenant_id=task.tenant_id,
            source_role=AgentSourceRole.TARGET,
            stable_locator="csv:target:3",
            stable_order=2,
            entity_kind=AgentEntityKind.STUDENT,
            category="学生",
            name="候选乙",
            number="S2",
            class_name="二班",
            phone="13800138002",
            email="s2@example.test",
            raw_row_number=3,
        ),
    )
    await AgentAnalysisRepository(session).persist_inputs(records)
    return task, run


@pytest.mark.asyncio
async def test_identity_conflict_does_not_also_emit_missing_work_or_analysis_batch(
    session,
) -> None:
    _task, run = await _identity_conflict_run(session)

    await AgentIdentityIndexBuilder(session).build(run_id=run.id)

    work_items = tuple(
        await session.scalars(
            select(AgentWorkItemRecord).order_by(AgentWorkItemRecord.kind)
        )
    )
    assert [item.kind for item in work_items] == ["correct", "identity_conflict"]
    assert await AgentBatchPlanner(session).create_for_run(run_id=run.id) == ()


@pytest.mark.asyncio
async def test_confirmed_identity_candidate_becomes_one_claimed_field_difference(
    session,
) -> None:
    _task, run = await _identity_conflict_run(session)
    builder = AgentIdentityIndexBuilder(session)
    await builder.build(run_id=run.id)
    clarification = await session.scalar(
        select(AgentClarificationRecord).where(
            AgentClarificationRecord.run_id == run.id
        )
    )
    assert clarification is not None
    selected_candidate_id = UUID(
        next(
            candidate["id"]
            for candidate in clarification.masked_candidates
            if candidate["number"] == "S1"
        )
    )
    governance = AgentGovernanceRepository(session)
    await governance.record_structured_clarification_selection(
        clarification.id,
        tenant_id="school-1",
        decision="select_candidate",
        selected_candidate_id=selected_candidate_id,
        note=None,
        interpretation_zh="你选择了第三方候选 A，确认后继续。",
        idempotency_key="identity-resolution-1",
        actor_id="operator-1",
    )
    await governance.confirm_clarification(
        clarification.id,
        actor_id="operator-1",
        confirmed=True,
    )

    resolved = await builder.resolve_confirmed_conflicts(run_id=run.id)
    batches = await AgentBatchPlanner(session).create_for_run(
        run_id=run.id,
        work_item_ids=tuple(item.id for item in resolved),
    )

    assert [item.kind for item in resolved] == ["field_difference"]
    assert [batch.item_count for batch in batches] == [1]
    batch_work_ids = tuple(
        await session.scalars(
            select(AgentModelBatchItemRecord.work_item_id).where(
                AgentModelBatchItemRecord.batch_id == batches[0].id
            )
        )
    )
    assert batch_work_ids == (resolved[0].id,)
    claim = await session.scalar(
        select(AgentIdentityClaimRecord).where(
            AgentIdentityClaimRecord.work_item_id == resolved[0].id
        )
    )
    assert claim is not None
    assert claim.authority_input_id == selected_candidate_id
    await session.refresh(clarification)
    assert clarification.interpretation is not None
    assert clarification.interpretation["resolved_work_item_id"] == str(resolved[0].id)

    replayed = await builder.resolve_confirmed_conflicts(run_id=run.id)
    replayed_batches = await AgentBatchPlanner(session).create_for_run(
        run_id=run.id,
        work_item_ids=tuple(item.id for item in replayed),
    )

    assert [item.id for item in replayed] == [resolved[0].id]
    assert [batch.id for batch in replayed_batches] == [batches[0].id]

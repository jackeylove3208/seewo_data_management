from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.ai.agent_batching import AgentBatchPlanner
from app.models.agent_analysis import AgentInputRecord, AgentWorkItemRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.reconciliation.agent_identity import AgentIdentityIndexBuilder
from app.repositories.agent_analysis import AgentAnalysisRepository
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

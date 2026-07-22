from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_runtime.csv_analysis_handlers import AgentIngestionPhaseHandler
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.models.agent_analysis import AgentConnectorCapabilityRecord, AgentInputMarkRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.agent_analysis import AgentAnalysisRepository


@pytest.mark.asyncio
async def test_handler_persists_csv_projections_marks_capabilities_and_safe_checkpoint(
    session, tmp_path: Path
) -> None:
    authority = tmp_path / "authority.csv"
    authority.write_text(
        "category,name,number,class,phone,email\n"
        "student,李四,S-1,一班,13800138000,\n",
        encoding="utf-8",
    )
    target = tmp_path / "target.csv"
    target.write_text(
        "category,name,number,class,phone,email\nstudent,李四,S-1,一班,,\n",
        encoding="utf-8",
    )
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        workflow_version="new-agent-v1",
        idempotency_key=f"ingestion-{uuid4()}",
        request_hash="a" * 64,
    )
    session.add(task)
    await session.flush()
    run = await AgentRuntimeRepository(session).create_run(
        task_id=task.id, tenant_id=task.tenant_id, conversation_id=None, kind=AgentRunKind.SYNC,
    )
    snapshots = []
    for role, path in (("authoritative", authority), ("target", target)):
        source = SourceFile(
            task_id=task.id,
            source_role=role,
            original_name=path.name,
            storage_name=f"{uuid4()}.csv",
            storage_path=str(path),
            sha256=uuid4().hex * 2,
            size_bytes=path.stat().st_size,
        )
        session.add(source)
        await session.flush()
        snapshot = Snapshot(
            id=uuid4(), task_id=task.id, source_file_id=source.id, source_role=role,
            schema_version="agent-contract-v1", mapping_version="agent-contract-v1",
            file_hash=source.sha256, content_hash=uuid4().hex * 2, summary={},
        )
        session.add(snapshot)
        snapshots.append(snapshot)
    await session.flush()

    await AgentIngestionPhaseHandler(session).ingest(run_id=run.id)

    analysis = AgentAnalysisRepository(session)
    assert len(await analysis.list_inputs(run.id, "authoritative")) == 1
    assert len(await analysis.list_inputs(run.id, "target")) == 1
    mark = await session.scalar(select(AgentInputMarkRecord))
    assert mark is not None and mark.reason_code == "authority_required_fields_missing"
    assert "13800138000" not in str(mark.safe_evidence)
    assert len(tuple(await session.scalars(select(AgentConnectorCapabilityRecord)))) == 2
    events = await AgentRuntimeRepository(session).list_events(run.id)
    assert events[-1].event_type == "agent_ingestion_persisted"
    assert "13800138000" not in str(events[-1].payload)

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_graph.contracts import AllowedActionV1
from app.agent_graph.evidence import EvidenceManifestV1
from app.agent_graph.production_executor import (
    ProductionGraphActionExecutor,
    _record_manifest,
)
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.runtime import ProductionGraphCandidateProvider
from app.agent_graph.worker import GraphActionOutcome, GraphWorkContext
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.models.agent_analysis import AgentGovernancePlanRecord
from app.models.agent_graph import AgentEvidenceManifestRecord, AgentHumanGateRecord
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile


class ModelMustNotRun:
    async def complete_json_once(self, _request):
        raise AssertionError("deterministic preflight called a model")


async def _preflight_context(database, tmp_path: Path) -> GraphWorkContext:
    async with database.session_factory() as session:
        async with session.begin():
            task = ReconciliationTask(
                tenant_id="school-preflight",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="governance",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=uuid4().hex * 2,
            )
            session.add(task)
            await session.flush()
            snapshots: dict[str, Snapshot] = {}
            for role in ("authoritative", "target"):
                path = tmp_path / f"{role}.csv"
                path.write_text("category,name\nstudent,测试学生\n", encoding="utf-8")
                source = SourceFile(
                    task_id=task.id,
                    source_role=role,
                    original_name=path.name,
                    storage_name=f"{uuid4()}.csv",
                    storage_path=str(path),
                    sha256=role[0] * 64,
                    size_bytes=path.stat().st_size,
                    detected_encoding="utf-8",
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
                    content_hash=role[-1] * 64,
                    state="published",
                    summary={},
                )
                session.add(snapshot)
                snapshots[role] = snapshot
            run = await AgentRuntimeRepository(session).create_run(
                task_id=task.id,
                tenant_id=task.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.SYNC,
                workflow_version="agent-graph-v1",
            )
            graph = await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-sync-graph-v1",
                initial_node="preflight_execution",
            )
            session.add(
                TargetVersionRecord(
                    id=uuid4(),
                    parent_version_id=None,
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    source_snapshot_id=snapshots["target"].id,
                    batch_id=None,
                    file_sha256="b" * 64,
                    content_hash="c" * 64,
                    storage_path=str(tmp_path / "current-target.csv"),
                )
            )
            session.add(
                AgentGovernancePlanRecord(
                    id=uuid4(),
                    run_id=run.id,
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    source_snapshot_id=snapshots["authoritative"].id,
                    target_snapshot_id=snapshots["target"].id,
                    target_version=f"sha256:{'a' * 64}",
                    finding_ids=[],
                    operations=[],
                    content_hash="d" * 64,
                    status="compiled",
                    compiled_by="test",
                )
            )
            await session.flush()
            return GraphWorkContext(
                worker_id="preflight-worker",
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                graph_run_id=graph.id,
                graph_version=graph.graph_version,
                current_node=graph.current_node,
                graph_cursor=graph.cursor,
                attempt_count=run.attempt_count,
                lease_token=uuid4(),
            )


@pytest.mark.asyncio
async def test_stale_preflight_requires_frozen_cross_phase_replan(
    database,
    tmp_path: Path,
) -> None:
    context = await _preflight_context(database, tmp_path)
    candidate_plan = await ProductionGraphCandidateProvider(
        database.session_factory
    )(context)
    allowed = tuple(
        item.action for item in candidate_plan.candidate_evaluations if item.passed
    )
    rejected = {
        item.action.action_id: item.rejected_guard_codes
        for item in candidate_plan.candidate_evaluations
        if not item.passed
    }

    assert [item.action_id for item in allowed] == ["request_cross_phase_replan"]
    assert rejected == {
        "execute_ready_operations": ("target_version_stale",),
    }

    outcome = await ProductionGraphActionExecutor(
        database.session_factory,
        provider=ModelMustNotRun(),
        tokenization_secret="test-tokenization-secret",
    )(context, allowed[0])

    assert outcome.pause_for_human is True
    async with database.session_factory() as session:
        gate = await session.scalar(
            select(AgentHumanGateRecord).where(
                AgentHumanGateRecord.graph_run_id == context.graph_run_id,
                AgentHumanGateRecord.gate_kind == "cross_phase_replan",
            )
        )
        assert gate is not None
        assert gate.status == "pending"
        assert gate.member_ids


@pytest.mark.asyncio
async def test_production_manifest_binds_opaque_tenant_snapshots_and_target_version(
    database,
    tmp_path: Path,
) -> None:
    context = await _preflight_context(database, tmp_path)
    candidate_plan = await ProductionGraphCandidateProvider(
        database.session_factory
    )(context)
    action = next(
        item.action for item in candidate_plan.candidate_evaluations if item.passed
    )

    async with database.session_factory() as session:
        async with session.begin():
            manifest_id = await _record_manifest(
                session,
                context=context,
                action=action,
                tokenization_secret="test-tokenization-secret",
            )
            record = await session.get(AgentEvidenceManifestRecord, manifest_id)

    assert record is not None
    manifest = EvidenceManifestV1.model_validate(record.manifest)
    assert context.tenant_id not in manifest.tenant_ref
    assert manifest.snapshot_pair is not None
    assert len(manifest.snapshot_pair) == 2
    assert manifest.target_version == f"sha256:{'b' * 64}"


@pytest.mark.asyncio
async def test_production_manifest_replay_reuses_frozen_manifest(
    database,
    tmp_path: Path,
) -> None:
    context = await _preflight_context(database, tmp_path)
    candidate_plan = await ProductionGraphCandidateProvider(
        database.session_factory
    )(context)
    action = next(
        item.action for item in candidate_plan.candidate_evaluations if item.passed
    )

    async with database.session_factory() as session:
        async with session.begin():
            first_id = await _record_manifest(
                session,
                context=context,
                action=action,
                tokenization_secret="test-tokenization-secret",
            )
            replay_id = await _record_manifest(
                session,
                context=context,
                action=action,
                tokenization_secret="test-tokenization-secret",
            )
            records = tuple(
                await session.scalars(
                    select(AgentEvidenceManifestRecord).where(
                        AgentEvidenceManifestRecord.graph_run_id
                        == context.graph_run_id,
                        AgentEvidenceManifestRecord.cursor == context.graph_cursor,
                        AgentEvidenceManifestRecord.action_id == action.action_id,
                    )
                )
            )

    assert replay_id == first_id
    assert len(records) == 1


@pytest.mark.asyncio
async def test_repair_analysis_action_dispatches_the_real_analysis_executor(
    database,
    tmp_path: Path,
) -> None:
    context = replace(
        await _preflight_context(database, tmp_path),
        current_node="repair_analysis_batch",
    )
    action = AllowedActionV1(
        action_id="repair_batch_12345678",
        graph_action_kind="repair_analysis_batch",
        kind="dispatch_sub_agent",
        sub_agent="reconciliation-analysis",
        resource_ids=("work-item:00000000-0000-0000-0000-000000000001",),
        required_evidence=(
            "paired-record:00000000-0000-0000-0000-000000000001",
        ),
        risk="low",
        requires_human=False,
        successor_node="analyze_actionable_batches",
    )

    class RecordingExecutor(ProductionGraphActionExecutor):
        analysis_dispatched = False

        async def _analyze_batch(self, _context, selected):
            self.analysis_dispatched = True
            return GraphActionOutcome(
                action_id=selected.action_id,
                evidence_refs=selected.required_evidence,
            )

    executor = RecordingExecutor(
        database.session_factory,
        provider=ModelMustNotRun(),
        tokenization_secret="test-tokenization-secret",
    )
    outcome = await executor(context, action)

    assert executor.analysis_dispatched is True
    assert outcome.action_id == action.action_id

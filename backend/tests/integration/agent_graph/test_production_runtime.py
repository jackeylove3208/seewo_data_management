from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
from app.agent_runtime.state_machine import AgentPhase, AgentRunKind
from app.ai.graph_subagents import GraphSubAgentFailure
from app.ai.providers.base import LLMResponse, ModelUsage
from app.models.agent_analysis import (
    AgentGovernancePlanRecord,
    AgentModelBatchRecord,
)
from app.models.agent_graph import (
    AgentEvidenceManifestRecord,
    AgentHumanGateRecord,
    AgentSubAgentInvocationRecord,
    AgentToolCallRecord,
)
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.agent_analysis import AgentAnalysisRepository
from app.schemas.agent_ingestion import (
    AgentContractRecord,
    AgentEntityKind,
    AgentSourceRole,
)


class ModelMustNotRun:
    async def complete_json_once(self, _request):
        raise AssertionError("deterministic preflight called a model")


class InvalidManifestResourceProvider:
    def __init__(self) -> None:
        self.requests = []

    async def complete_json_once(self, request):
        self.requests.append(request)
        return LLMResponse(
            output={
                "result": {
                    "tool_call": {
                        "name": "read_work_item",
                        "arguments": {
                            "resource_id": "work-item:00000000-0000-0000-0000-000000000000"
                        },
                    }
                }
            },
            provider="scripted",
            model="scripted-long-context",
            request_id=f"request-{len(self.requests)}",
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )


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


@pytest.mark.asyncio
async def test_failed_analysis_preserves_model_and_tool_audit_across_batch_reset(
    database,
) -> None:
    provider = InvalidManifestResourceProvider()
    worker_id = "analysis-audit-worker"
    async with database.session_factory() as session:
        async with session.begin():
            task = ReconciliationTask(
                tenant_id="school-analysis-audit",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["department"],
                status="running",
                stage="analysis",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=uuid4().hex * 2,
            )
            session.add(task)
            await session.flush()
            snapshots: dict[str, Snapshot] = {}
            for role in ("authoritative", "target"):
                source = SourceFile(
                    task_id=task.id,
                    source_role=role,
                    original_name=f"{role}.csv",
                    storage_name=f"{uuid4()}.csv",
                    storage_path=f"/synthetic/{uuid4()}.csv",
                    sha256=uuid4().hex * 2,
                    size_bytes=1,
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
                    content_hash=uuid4().hex * 2,
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
            run.status = "running"
            run.phase = AgentPhase.ANALYZE_BATCHES.value
            run.lease_owner = worker_id
            run.lease_token = uuid4()
            run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
            graph = await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-sync-graph-v1",
                initial_node="analyze_actionable_batches",
            )
            repository = AgentAnalysisRepository(session)
            authority, target = await repository.persist_inputs(
                (
                    AgentContractRecord(
                        task_id=task.id,
                        run_id=run.id,
                        snapshot_id=snapshots["authoritative"].id,
                        tenant_id=task.tenant_id,
                        source_role=AgentSourceRole.AUTHORITATIVE,
                        stable_locator="csv:2",
                        stable_order=2,
                        entity_kind=AgentEntityKind.DEPARTMENT,
                        category="部门",
                        name="一年级",
                        number="D-001",
                        phone=None,
                        email=None,
                        class_name=None,
                    ),
                    AgentContractRecord(
                        task_id=task.id,
                        run_id=run.id,
                        snapshot_id=snapshots["target"].id,
                        tenant_id=task.tenant_id,
                        source_role=AgentSourceRole.TARGET,
                        stable_locator="csv:2",
                        stable_order=2,
                        entity_kind=AgentEntityKind.DEPARTMENT,
                        category="部门",
                        name="二年级",
                        number="D-001",
                        phone=None,
                        email=None,
                        class_name=None,
                    ),
                )
            )
            work_item = await repository.persist_work_item(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_snapshot_id=snapshots["authoritative"].id,
                target_snapshot_id=snapshots["target"].id,
                subject_input_id=target.id,
                entity_kind="department",
                kind="field_difference",
                idempotency_hash="a" * 64,
                evidence_hash="b" * 64,
            )
            await repository.persist_identity_claim(
                run_id=run.id,
                task_id=task.id,
                source_snapshot_id=snapshots["authoritative"].id,
                target_snapshot_id=snapshots["target"].id,
                authority_input_id=authority.id,
                target_input_id=target.id,
                work_item_id=work_item.id,
            )
            batch = await repository.create_or_get_batch(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                entity_kind="department",
                input_hash="c" * 64,
                work_item_ids=(work_item.id,),
            )
            assert run.lease_token is not None
            context = GraphWorkContext(
                worker_id=worker_id,
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                graph_run_id=graph.id,
                graph_version=graph.graph_version,
                current_node=graph.current_node,
                graph_cursor=graph.cursor,
                attempt_count=run.attempt_count,
                lease_token=run.lease_token,
            )
            action = AllowedActionV1(
                action_id=f"analyze_batch_{str(batch.id)[:8]}",
                graph_action_kind="analyze_next_batch",
                kind="dispatch_sub_agent",
                sub_agent="reconciliation-analysis",
                resource_ids=(f"work-item:{work_item.id}",),
                required_evidence=(f"paired-record:{work_item.id}",),
                risk="low",
                requires_human=False,
                successor_node="analyze_actionable_batches",
            )

    with pytest.raises(GraphSubAgentFailure) as captured:
        await ProductionGraphActionExecutor(
            database.session_factory,
            provider=provider,
            tokenization_secret="test-tokenization-secret",
        )(context, action)

    assert captured.value.failure_categories == ("tool_argument_rejected",)
    assert captured.value.attempt_count == 4
    async with database.session_factory() as session:
        invocations = tuple(
            await session.scalars(
                select(AgentSubAgentInvocationRecord)
                .where(AgentSubAgentInvocationRecord.graph_run_id == context.graph_run_id)
                .order_by(AgentSubAgentInvocationRecord.attempt)
            )
        )
        tool_calls = tuple(
            await session.scalars(
                select(AgentToolCallRecord).order_by(AgentToolCallRecord.created_at)
            )
        )
        saved_batch = await session.get(AgentModelBatchRecord, batch.id)
        manifest = await session.scalar(
            select(AgentEvidenceManifestRecord).where(
                AgentEvidenceManifestRecord.graph_run_id == context.graph_run_id
            )
        )

    assert len(provider.requests) == 4
    assert [item.status for item in invocations] == ["failed"] * 4
    assert {
        item.model_provenance["safe_error_code"] for item in invocations
    } == {"tool_argument_rejected"}
    assert len(tool_calls) == 4
    assert all(not item.authorized and item.status == "denied" for item in tool_calls)
    assert saved_batch is not None
    assert saved_batch.status == "pending"
    assert saved_batch.lease_owner is None
    assert saved_batch.lease_token is None
    assert manifest is not None

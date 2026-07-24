from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_graph.repository import AgentGraphRepository
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase, AgentRunKind
from app.ai.providers.base import LLMResponse
from app.core.config import Settings
from app.main import create_app
from app.models.agent_analysis import (
    AgentApprovalGroupRecord,
    AgentInputRecord,
    AgentWorkItemRecord,
)
from app.models.agent_graph import AgentGraphRunRecord
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.agent_governance import AgentGovernanceRepository


@pytest.fixture
def graph_agent_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'agent-graph-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        export_root=tmp_path / "exports",
        auto_create_schema=True,
        new_agent_enabled=True,
        agent_graph_enabled=True,
        new_agent_analysis_only=True,
        tokenization_secret="test-tokenization-secret",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_graph_progress_and_tenant_safe_gate_decision(
    graph_agent_client: TestClient,
) -> None:
    agent_client = graph_agent_client
    async def seed() -> tuple[str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="analysis",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
            )
            session.add(task)
            await session.flush()
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
                initial_node="aggregate_risk",
            )
            finding_ids = tuple(str(uuid4()) for _index in range(50))
            session.add(
                AgentApprovalGroupRecord(
                    run_id=run.id,
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    group_key="field_difference:student:update:agent-risk-v1",
                    membership_hash="membership-hash",
                    finding_ids=list(finding_ids),
                    issue_kind="field_difference",
                    entity_kind="student",
                    operation="update",
                    policy_version="agent-risk-v1",
                    risk="high",
                    status="pending",
                )
            )
            gate = await AgentGraphRepository(session).record_human_gate(
                graph_run_id=graph.id,
                cursor=graph.cursor,
                gate_kind="high_risk_approval",
                member_ids=finding_ids,
                content_hash="sha256:" + ("a" * 64),
                status="pending",
            )
            graph.current_node = "wait_high_risk_approvals"
            graph.cursor = 1
            run.status = "waiting_human"
            await session.commit()
            return str(task.id), str(gate.id)

    task_id, gate_id = agent_client.portal.call(seed)
    progress = agent_client.get(f"/api/agent/tasks/{task_id}/graph")

    assert progress.status_code == 200, progress.text
    body = progress.json()
    assert body["business_stage"] == "governance_execution"
    assert body["current_action_zh"] == "正在等待高风险操作审批"
    assert body["human_gates"][0]["item_count"] == 50
    assert "prompt" not in progress.text.casefold()
    assert "content_hash" not in progress.text

    override = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/{gate_id}/decision",
        json={
            "decision": "approve",
            "tenant_id": "other-school",
        },
    )
    assert override.status_code == 422

    approved = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/{gate_id}/decision",
        json={"decision": "approve"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    progress_after_decision = agent_client.get(
        f"/api/agent/tasks/{task_id}/graph"
    )
    assert progress_after_decision.json()["status"] == "running"


def test_rejected_rollback_gate_requests_safe_termination(
    graph_agent_client: TestClient,
) -> None:
    agent_client = graph_agent_client

    async def seed() -> tuple[str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="rollback",
                workflow_version="agent-graph-v1",
                task_kind="rollback",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
            )
            session.add(task)
            await session.flush()
            run = await AgentRuntimeRepository(session).create_run(
                task_id=task.id,
                tenant_id=task.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.ROLLBACK,
                workflow_version="agent-graph-v1",
            )
            graph = await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-rollback-graph-v1",
                initial_node="wait_restore_conflicts",
            )
            gate = await AgentGraphRepository(session).record_human_gate(
                graph_run_id=graph.id,
                cursor=graph.cursor,
                gate_kind="rollback_conflict",
                member_ids=(str(uuid4()),),
                content_hash="sha256:" + ("b" * 64),
                status="pending",
            )
            graph.current_node = "wait_restore_conflicts"
            graph.cursor = 1
            run.status = "waiting_human"
            await session.commit()
            return str(task.id), str(gate.id)

    task_id, gate_id = agent_client.portal.call(seed)
    rejected = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/{gate_id}/decision",
        json={"decision": "reject", "reason": "不允许继续回滚"},
    )

    assert rejected.status_code == 200, rejected.text

    async def inspect() -> tuple[bool, str]:
        async with agent_client.app.state.database.session_factory() as session:
            run = await session.scalar(
                select(AgentRunRecord).where(
                    AgentRunRecord.task_id == UUID(task_id)
                )
            )
            assert run is not None
            graph = await session.scalar(
                select(AgentGraphRunRecord).where(
                    AgentGraphRunRecord.run_id == run.id
                )
            )
            assert graph is not None
            return graph.termination_requested, run.status

    termination_requested, run_status = agent_client.portal.call(inspect)
    assert termination_requested is True
    assert run_status == "running"


def test_termination_requires_preview_and_explicit_confirmation(
    graph_agent_client: TestClient,
) -> None:
    agent_client = graph_agent_client

    async def seed() -> str:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="ingestion",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
            )
            session.add(task)
            await session.flush()
            run = await AgentRuntimeRepository(session).create_run(
                task_id=task.id,
                tenant_id=task.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.SYNC,
                workflow_version="agent-graph-v1",
            )
            await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-sync-graph-v1",
                initial_node="inspect_sources",
            )
            await session.commit()
            return str(task.id)

    task_id = agent_client.portal.call(seed)
    preview = agent_client.post(
        f"/api/agent/tasks/{task_id}/termination-preview"
    )

    assert preview.status_code == 200, preview.text
    gate = preview.json()
    assert gate["kind"] == "termination_confirmation"
    assert gate["status"] == "pending"

    async def termination_requested() -> bool:
        async with agent_client.app.state.database.session_factory() as session:
            run = await session.scalar(
                select(AgentRunRecord).where(
                    AgentRunRecord.task_id == UUID(task_id)
                )
            )
            assert run is not None
            graph = await AgentGraphRepository(
                session
            ).get_run_state_for_agent_run(run.id)
            assert graph is not None
            return graph.termination_requested

    assert agent_client.portal.call(termination_requested) is False
    confirmed = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/{gate['id']}/decision",
        json={"decision": "approve", "reason": "确认终止任务"},
    )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "approved"
    assert agent_client.portal.call(termination_requested) is True


def test_identity_conflict_uses_skill_model_and_requires_second_confirmation(
    graph_agent_client: TestClient,
) -> None:
    agent_client = graph_agent_client

    async def seed() -> tuple[str, str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="analysis",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
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
                    storage_path=f"/synthetic/{role}.csv",
                    sha256=role[0] * 64,
                    size_bytes=10,
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
            run.phase = AgentPhase.CLARIFY_IDENTITY_CONFLICTS.value
            run.status = "waiting_human"
            graph = await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-sync-graph-v1",
                initial_node="resolve_identity_conflicts",
            )
            target_input = AgentInputRecord(
                run_id=run.id,
                task_id=task.id,
                snapshot_id=snapshots["target"].id,
                tenant_id=task.tenant_id,
                source_role="target",
                stable_locator="csv:1",
                stable_order=1,
                entity_kind="student",
                category="student",
                name="测试学生",
                number="S-001",
                class_name="一年级一班",
                phone="token:student-phone",
                email="student@example.test",
                raw_row_number=1,
                input_hash="e" * 64,
            )
            session.add(target_input)
            await session.flush()
            work_item = AgentWorkItemRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_snapshot_id=snapshots["authoritative"].id,
                target_snapshot_id=snapshots["target"].id,
                subject_input_id=target_input.id,
                entity_kind="student",
                kind="identity_conflict",
                state="awaiting_clarification",
                idempotency_hash="f" * 64,
                evidence_hash="1" * 64,
            )
            session.add(work_item)
            await session.flush()
            candidate_id = uuid4()
            clarification = await AgentGovernanceRepository(
                session
            ).create_clarification(
                run=run,
                task=task,
                work_item_id=work_item.id,
                candidates=(
                    {
                        "id": str(candidate_id),
                        "number": "S-001",
                        "phone": "138****0001",
                    },
                ),
                allowed_outcomes=("use_candidate", "target_extra"),
            )
            await AgentGraphRepository(session).record_human_gate(
                graph_run_id=graph.id,
                cursor=graph.cursor,
                gate_kind="identity_conflict",
                member_ids=(str(clarification.id),),
                content_hash="sha256:" + ("9" * 64),
                status="pending",
            )
            graph.cursor = 1
            await session.commit()
            return str(task.id), str(clarification.id), str(candidate_id)

    task_id, clarification_id, candidate_id = agent_client.portal.call(seed)
    resource_id = f"identity-conflict:{clarification_id}"
    draft = {
        "schema_version": "agent-contract-v1",
        "conflict_id": clarification_id,
        "decision": "select_candidate",
        "selected_candidate_id": candidate_id,
        "interpretation_zh": "我理解为选择编号 S-001 的候选，确认后继续。",
        "requires_second_confirmation": True,
    }

    class ConflictProvider:
        def __init__(self) -> None:
            self.outputs = [
                {
                    "result": {
                        "tool_call": {
                            "name": "read_frozen_conflict",
                            "arguments": {"resource_id": resource_id},
                        }
                    }
                },
                {
                    "result": {
                        "tool_call": {
                            "name": "submit_conflict_interpretation",
                            "arguments": {"resource_id": resource_id, **draft},
                        }
                    }
                },
                {"result": draft},
            ]

        async def complete_json_once(self, _request) -> LLMResponse:
            return LLMResponse(
                output=self.outputs.pop(0),
                provider="scripted",
                model="conflict-model",
                request_id=str(uuid4()),
            )

    agent_client.app.state.graph_skill_provider = ConflictProvider()
    interpreted = agent_client.post(
        f"/api/agent/tasks/{task_id}/clarification",
        json={"message": "请选择编号 S-001 的候选。"},
    )

    assert interpreted.status_code == 200, interpreted.text
    assert interpreted.json() == {
        "decision_id": clarification_id,
        "status": "interpreted",
        "task_id": task_id,
        "decision": "select_candidate",
        "selected_candidate_id": candidate_id,
        "interpretation_zh": "我理解为选择编号 S-001 的候选，确认后继续。",
        "requires_second_confirmation": True,
    }

    confirmed = agent_client.post(
        f"/api/agent/tasks/{task_id}/clarification/{clarification_id}/confirm",
        json={},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

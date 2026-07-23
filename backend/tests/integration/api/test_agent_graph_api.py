from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agent_graph.repository import AgentGraphRepository
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.core.config import Settings
from app.main import create_app
from app.models.reconciliation import ReconciliationTask


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
                initial_node="wait_high_risk_approvals",
            )
            gate = await AgentGraphRepository(session).record_human_gate(
                graph_run_id=graph.id,
                cursor=graph.cursor,
                gate_kind="high_risk_approval",
                member_ids=tuple(str(uuid4()) for _index in range(50)),
                content_hash="sha256:" + ("a" * 64),
                status="pending",
            )
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

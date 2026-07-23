from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agent_reporting.service import AgentReportingService
from app.ai.providers.base import LLMRequest, LLMResponse
from app.core.config import Settings
from app.main import create_app
from app.models.reconciliation import ReconciliationTask


class ConversationProvider:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            output={
                "result": {
                    "kind": "start_confirmation",
                    "title": "本地学生同步",
                    "entity_types": ["student"],
                    "source_ref": "third-party/roster.csv",
                    "target_ref": "seewo/roster.csv",
                    "message_zh": "已确认两份本地数据。",
                }
            },
            provider="stub",
            model="stub",
        )


@pytest.fixture
def agent_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'agent-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        export_root=tmp_path / "exports",
        auto_create_schema=True,
        new_agent_enabled=True,
        new_agent_analysis_only=True,
        tokenization_secret="test-tokenization-secret",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _upload(client: TestClient, tmp_path: Path, role: str, name: str) -> str:
    path = tmp_path / name
    path.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n"
        "学生,张三,S001,一班,13800000001,student@example.test\n",
        encoding="utf-8",
    )
    with path.open("rb") as handle:
        response = client.post(
            "/api/uploads",
            data={"source_role": role},
            files={"file": (name, handle, "text/csv")},
        )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_manual_csv_task_uses_agent_runtime_and_exposes_persisted_events(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(agent_client, tmp_path, "authoritative", "authority.csv")
    target_id = _upload(agent_client, tmp_path, "target", "target.csv")

    created = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "agent-manual-1"},
        json={
            "title": "全校学生同步",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "csv", "upload_id": target_id},
        },
    )

    assert created.status_code == 202, created.text
    task = created.json()
    assert task["workflow_version"] == "new-agent-v1"
    assert task["phase"] == "ingest_and_normalize"
    assert task["status"] == "running"
    assert task["title"] == "全校学生同步"

    history = agent_client.get("/api/agent/history")
    assert history.status_code == 200
    assert history.json()["items"][0]["id"] == task["id"]
    assert history.json()["items"][0]["completed_at"] is None

    fetched = agent_client.get(f"/api/agent/tasks/{task['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == task["id"]

    events = agent_client.get(f"/api/agent/tasks/{task['id']}/events")
    assert events.status_code == 200
    body = events.json()
    assert body["cursor"] == "3"
    assert [event["type"] for event in body["events"]] == [
        "run.created",
        "school_lock.acquired",
        "phase.started",
    ]


def test_agent_task_start_returns_stable_school_lock_conflict(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(agent_client, tmp_path, "authoritative", "authority-lock.csv")
    target_id = _upload(agent_client, tmp_path, "target", "target-lock.csv")
    intent = {
        "title": "全校学生同步",
        "entity_types": ["student"],
        "source": {"kind": "csv", "upload_id": source_id},
        "target": {"kind": "csv", "upload_id": target_id},
    }
    first = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "agent-lock-owner"},
        json=intent,
    )
    assert first.status_code == 202

    blocked = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "agent-lock-contender"},
        json=intent,
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "school_lock_conflict"
    assert blocked.json()["detail"]["owner_task_id"] == first.json()["id"]

    active_lock = agent_client.get("/api/agent/active-lock")
    assert active_lock.status_code == 200
    assert active_lock.json() == {
        "active": True,
        "owner_task_id": first.json()["id"],
        "owner_run_id": active_lock.json()["owner_run_id"],
        "acquired_at": active_lock.json()["acquired_at"],
        "heartbeat_at": active_lock.json()["heartbeat_at"],
    }


def test_agent_api_rejects_client_tenant_override(
    agent_client: TestClient,
) -> None:
    response = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "spoofed-agent-tenant"},
        json={
            "title": "越权任务",
            "tenant_id": "other-school",
            "entity_types": ["student"],
            "source": {"kind": "api", "configuration_id": "authority"},
            "target": {"kind": "api", "configuration_id": "target"},
        },
    )

    assert response.status_code == 422


def test_configured_connector_is_rejected_before_task_and_lock_are_created(
    agent_client: TestClient,
) -> None:
    response = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "missing-configured-connector"},
        json={
            "title": "配置连接器同步",
            "entity_types": ["student"],
            "source": {"kind": "api", "configuration_id": "authority"},
            "target": {"kind": "database", "configuration_id": "seewo"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "connector_capability_failure"
    assert agent_client.get("/api/agent/active-lock").json() == {
        "active": False,
        "owner_task_id": None,
        "owner_run_id": None,
        "acquired_at": None,
        "heartbeat_at": None,
    }
    assert agent_client.get("/api/agent/history").json()["items"] == []


def test_conversation_uses_model_discovered_local_sources(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "local-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,a@example.test\n",
            encoding="utf-8",
        )
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    provider = ConversationProvider()
    agent_client.app.state.conversation_provider = provider

    conversation = agent_client.post("/api/agent/conversations")
    message = agent_client.post(
        f"/api/agent/conversations/{conversation.json()['id']}/messages",
        json={"message": "同步本地学生数据"},
    )

    assert message.status_code == 200, message.text
    assert message.json()["message"] == "已确认两份本地数据。"
    assert message.json()["intent"]["source"] == {
        "kind": "local", "source_ref": "third-party/roster.csv"
    }
    assert "converse-school-data-sync@1.0.0" in provider.requests[0].messages[0].content

    created = agent_client.post(
        f"/api/agent/conversations/{conversation.json()['id']}/tasks",
        headers={"Idempotency-Key": "agent-local-conversation-1"},
        json={
            "title": "浏览器篡改标题",
            "entity_types": ["teacher"],
            "source": {"kind": "local", "source_ref": "third-party/other.csv"},
            "target": {"kind": "local", "source_ref": "seewo/other.csv"},
        },
    )

    assert created.status_code == 202, created.text
    assert created.json()["title"] == "本地学生同步"
    assert agent_client.get("/api/agent/history").json()["items"][0]["id"] == created.json()["id"]


def test_termination_persists_history_before_releasing_school_lock(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(agent_client, tmp_path, "authoritative", "terminate-source.csv")
    target_id = _upload(agent_client, tmp_path, "target", "terminate-target.csv")
    created = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "agent-terminate-1"},
        json={
            "title": "待终止同步",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "csv", "upload_id": target_id},
        },
    )
    task_id = created.json()["id"]

    terminated = agent_client.post(f"/api/agent/tasks/{task_id}/terminate")
    assert terminated.status_code == 200, terminated.text
    assert terminated.json()["status"] == "terminated"

    history = agent_client.get("/api/agent/history")
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["id"] == task_id
    assert history.json()["items"][0]["status"] == "terminated"
    assert history.json()["items"][0]["deletion_eligible"] is True

    deleted = agent_client.delete(f"/api/agent/tasks/{task_id}")
    assert deleted.status_code == 204, deleted.text


def test_rollback_preview_requires_a_separate_confirmation_before_locking(
    agent_client: TestClient,
) -> None:
    target_version_id = uuid4()

    async def seed_completed_task() -> str:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="completed",
                stage="terminal",
                workflow_version="new-agent-v1",
                task_kind="sync",
                title="已治理任务",
                idempotency_key=f"rollback-source-{uuid4()}",
                request_hash="e" * 64,
            )
            session.add(task)
            await session.flush()
            await AgentReportingService(session).generate(
                task_id=task.id,
                tenant_id=task.tenant_id,
                kind="sync",
                terminal_state="completed",
                facts={
                    "output_target_version_id": str(target_version_id),
                    "mutations": [
                        {
                            "id": str(uuid4()),
                            "status": "succeeded",
                            "verification": {"valid": True},
                            "operation": "update",
                            "entity_kind": "student",
                            "target_source_identifier": "csv:2",
                            "before": {"name": "旧姓名"},
                            "after": {"name": "新姓名"},
                        }
                    ],
                },
            )
            await session.commit()
            return str(task.id)

    rejected_source_task_id = agent_client.portal.call(seed_completed_task)
    rejected_preview = agent_client.post(
        f"/api/agent/tasks/{rejected_source_task_id}/rollback-preview"
    )
    rejected = agent_client.post(
        f"/api/agent/rollback-tasks/{rejected_preview.json()['task_id']}/reject"
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "terminated"
    assert rejected.json()["report_id"] is not None

    source_task_id = agent_client.portal.call(seed_completed_task)
    preview = agent_client.post(
        f"/api/agent/tasks/{source_task_id}/rollback-preview"
    )
    assert preview.status_code == 201, preview.text
    assert preview.json()["requires_confirmation"] is True
    rollback_task_id = preview.json()["task_id"]

    task_before_confirmation = agent_client.get(
        f"/api/agent/tasks/{rollback_task_id}"
    )
    assert task_before_confirmation.json()["phase"] == "intent_confirmed"
    assert task_before_confirmation.json()["status"] == "pending"

    confirmed = agent_client.post(
        f"/api/agent/rollback-tasks/{rollback_task_id}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["phase"] == "plan_restore"
    assert confirmed.json()["status"] == "running"

    terminated = agent_client.post(f"/api/agent/tasks/{rollback_task_id}/terminate")
    assert terminated.status_code == 200, terminated.text
    assert terminated.json()["status"] == "terminated"
    rollback_history = agent_client.get("/api/agent/history").json()["items"]
    rollback_item = next(item for item in rollback_history if item["id"] == rollback_task_id)
    assert rollback_item["task_kind"] == "rollback"
    assert rollback_item["parent_task_id"] == source_task_id


def test_agent_report_api_masks_student_phone_and_is_tenant_scoped(
    agent_client: TestClient,
) -> None:
    async def seed_report() -> tuple[str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="completed",
                stage="terminal",
                workflow_version="new-agent-v1",
                task_kind="sync",
                title="隐私报告",
                idempotency_key=f"private-report-{uuid4()}",
                request_hash="f" * 64,
            )
            session.add(task)
            await session.flush()
            report = await AgentReportingService(session).generate(
                task_id=task.id,
                tenant_id=task.tenant_id,
                kind="sync",
                terminal_state="completed",
                facts={
                    "mutations": [
                        {
                            "id": "op-private",
                            "status": "succeeded",
                            "before": {"phone": "13800138000"},
                            "after": {"phone": "13900139000"},
                            "verification": {"valid": True},
                        }
                    ]
                },
                narrative={"summary": "手机号 13800138000 已更新"},
            )
            await session.commit()
            return str(task.id), str(report.id)

    task_id, report_id = agent_client.portal.call(seed_report)
    response = agent_client.get(f"/api/agent/tasks/{task_id}/report")

    assert response.status_code == 200
    assert response.json()["id"] == report_id
    serialized = response.text
    assert "13800138000" not in serialized
    assert "13900139000" not in serialized
    assert "***8000" in serialized
    assert "***9000" in serialized

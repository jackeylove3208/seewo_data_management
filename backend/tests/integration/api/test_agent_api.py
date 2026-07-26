import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_reporting.service import AgentReportingService
from app.ai.providers.base import LLMRequest, LLMResponse
from app.api.dependencies import get_operator_context
from app.core.config import Settings
from app.core.security import OperatorContext
from app.main import create_app
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile


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


class InvalidConversationProvider:
    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        del request
        return LLMResponse(
            output={"unexpected": "shape"},
            provider="stub",
            model="stub",
        )


class IncrementalConversationProvider:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            output = {
                "result": {
                    "kind": "intent_update",
                    "title": "学生同步",
                    "entity_types": ["student"],
                    "message_zh": "已记住要同步学生，请继续选择数据来源。",
                }
            }
        else:
            output = {
                "result": {
                    "kind": "clarification",
                    "message_zh": "我会沿用上一轮已确认的学生范围。",
                }
            }
        return LLMResponse(output=output, provider="stub", model="stub")


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


def test_graph_flag_routes_only_new_tasks_to_agent_graph_version(
    graph_agent_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(graph_agent_client, tmp_path, "authoritative", "graph-authority.csv")
    target_id = _upload(graph_agent_client, tmp_path, "target", "graph-target.csv")

    created = graph_agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "agent-graph-manual-1"},
        json={
            "title": "全校学生图同步",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "csv", "upload_id": target_id},
        },
    )

    assert created.status_code == 202, created.text
    assert created.json()["workflow_version"] == "agent-graph-v1"


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
    agent_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )
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


def test_local_source_api_returns_only_safe_server_capabilities(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "listed-local-sources"
    for relative in ("data/authority.csv", "seewo/target.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("编号,姓名\n001,测试", encoding="utf-8")
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )

    response = agent_client.get("/api/agent/local-sources")

    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "source_ref": "data/authority.csv",
            "kind": "csv",
            "writable_as_target": False,
        },
        {
            "source_ref": "seewo/target.csv",
            "kind": "csv",
            "writable_as_target": True,
        },
    ]
    assert str(root) not in response.text


def test_local_task_rejects_target_outside_server_write_roots(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "read-only-local-target"
    for relative in ("data/authority.csv", "readonly/target.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("编号,姓名\n001,测试", encoding="utf-8")
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )

    response = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "read-only-local-target"},
        json={
            "title": "不应启动的本地任务",
            "entity_types": ["student"],
            "source": {"kind": "local", "source_ref": "data/authority.csv"},
            "target": {"kind": "local", "source_ref": "readonly/target.csv"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_agent_intent"
    assert agent_client.get("/api/agent/active-lock").json()["active"] is False


def test_uploaded_authority_can_bind_to_a_writable_local_target(
    graph_agent_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(
        graph_agent_client,
        tmp_path,
        "authoritative",
        "uploaded-authority.csv",
    )
    root = tmp_path / "mixed-local-target"
    target = root / "seewo/target.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n"
        "学生,张三,S001,一班,13800000002,student@example.test\n",
        encoding="utf-8",
    )
    graph_agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    graph_agent_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )

    response = graph_agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "uploaded-authority-local-target"},
        json={
            "title": "直接写回希沃原文件",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "local", "source_ref": "seewo/target.csv"},
        },
    )

    assert response.status_code == 202, response.text
    task_id = response.json()["id"]

    async def bound_files() -> list[tuple[str, str, bool]]:
        async with graph_agent_client.app.state.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        Snapshot.source_role,
                        SourceFile.storage_path,
                        SourceFile.managed_storage,
                    )
                    .join(SourceFile, SourceFile.id == Snapshot.source_file_id)
                    .where(Snapshot.task_id == UUID(task_id))
                    .order_by(Snapshot.source_role)
                )
            ).all()
            return [
                (str(role), str(path), bool(managed_storage))
                for role, path, managed_storage in rows
            ]

    files = graph_agent_client.portal.call(bound_files)
    assert [role for role, _path, _managed in files] == ["authoritative", "target"]
    assert files[1] == ("target", str(target), False)

    second_source_id = _upload(
        graph_agent_client,
        tmp_path,
        "authoritative",
        "second-uploaded-authority.csv",
    )
    graph_agent_client.app.dependency_overrides[get_operator_context] = lambda: OperatorContext(
        operator_id="second-demo-operator",
        tenant_id="second-demo-school",
    )
    repeated = graph_agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "repeat-same-local-target"},
        json={
            "title": "再次同步同一个希沃原文件",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": second_source_id},
            "target": {"kind": "local", "source_ref": "seewo/target.csv"},
        },
    )

    assert repeated.status_code == 202, repeated.text


def test_conversation_returns_sanitized_error_for_invalid_model_output(
    agent_client: TestClient,
) -> None:
    agent_client.app.state.conversation_provider = InvalidConversationProvider()
    conversation = agent_client.post("/api/agent/conversations")

    response = agent_client.post(
        f"/api/agent/conversations/{conversation.json()['id']}/messages",
        json={"message": "你是谁"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "conversation_model_error",
        "message": "对话模型暂时无法生成有效回复，请稍后重试。",
    }


def test_current_conversation_restores_persisted_messages_and_active_task(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "resumable-local-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,a@example.test\n",
            encoding="utf-8",
        )
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )
    agent_client.app.state.conversation_provider = ConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()

    message = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "同步本地学生数据"},
    )
    assert message.status_code == 200, message.text
    created = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/tasks",
        headers={"Idempotency-Key": "resumable-conversation-task"},
        json={
            "title": "客户端不会成为事实",
            "entity_types": ["teacher"],
            "source": {"kind": "local", "source_ref": "third-party/other.csv"},
            "target": {"kind": "local", "source_ref": "seewo/other.csv"},
        },
    )
    assert created.status_code == 202, created.text

    current = agent_client.get("/api/agent/conversations/current")

    assert current.status_code == 200, current.text
    body = current.json()
    assert body["id"] == conversation["id"]
    assert [(item["role"], item["text"]) for item in body["messages"]] == [
        ("user", "同步本地学生数据"),
        ("assistant", "已确认两份本地数据。"),
    ]
    assert body["intent"]["title"] == "本地学生同步"
    assert body["task"]["id"] == created.json()["id"]
    assert body["start_confirmation"] is None


def test_current_conversation_restores_an_unstarted_confirmation(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "confirmation-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("类别,姓名,编号,班级,电话,邮箱\n", encoding="utf-8")
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )
    agent_client.app.state.conversation_provider = ConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()
    sent = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "同步本地学生数据"},
    )
    assert sent.status_code == 200, sent.text

    current = agent_client.get("/api/agent/conversations/current")

    assert current.status_code == 200
    assert current.json()["task"] is None
    assert current.json()["start_confirmation"] == {
        "title": "本地学生同步",
        "summary": "已确认两份本地数据。",
        "entity_types": ["student"],
    }


def test_current_conversation_is_hidden_from_another_tenant(
    agent_client: TestClient,
) -> None:
    created = agent_client.post("/api/agent/conversations")
    assert created.status_code == 201

    agent_client.app.dependency_overrides[get_operator_context] = lambda: OperatorContext(
        operator_id="demo-operator",
        tenant_id="other-school",
    )
    try:
        current = agent_client.get("/api/agent/conversations/current")
    finally:
        agent_client.app.dependency_overrides.pop(get_operator_context, None)

    assert current.status_code == 200
    assert current.json() is None


def test_current_conversation_prioritizes_the_school_lock_owner_over_a_new_empty_chat(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "locked-conversation-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,a@example.test\n",
            encoding="utf-8",
        )
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )
    agent_client.app.state.conversation_provider = ConversationProvider()
    owner = agent_client.post("/api/agent/conversations").json()
    message = agent_client.post(
        f"/api/agent/conversations/{owner['id']}/messages",
        json={"message": "同步本地学生数据"},
    )
    assert message.status_code == 200, message.text
    started = agent_client.post(
        f"/api/agent/conversations/{owner['id']}/tasks",
        headers={"Idempotency-Key": "locked-conversation-owner"},
        json={
            "title": "客户端草案不作为事实",
            "entity_types": ["teacher"],
            "source": {"kind": "local", "source_ref": "third-party/other.csv"},
            "target": {"kind": "local", "source_ref": "seewo/other.csv"},
        },
    )
    assert started.status_code == 202, started.text

    newer_empty = agent_client.post("/api/agent/conversations")
    assert newer_empty.status_code == 201

    current = agent_client.get("/api/agent/conversations/current")

    assert current.status_code == 200
    assert current.json()["id"] == owner["id"]
    assert current.json()["task"]["id"] == started.json()["id"]


def test_failed_conversation_reply_is_persisted_as_recoverable_message(
    agent_client: TestClient,
) -> None:
    agent_client.app.state.conversation_provider = InvalidConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()

    response = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "你是谁"},
    )
    assert response.status_code == 502

    current = agent_client.get("/api/agent/conversations/current")

    assert [(item["role"], item["kind"]) for item in current.json()["messages"]] == [
        ("user", "normal"),
        ("assistant", "error"),
    ]
    assert "稍后重试" in current.json()["messages"][-1]["text"]


def test_conversation_model_receives_complete_persisted_history(
    agent_client: TestClient,
) -> None:
    provider = IncrementalConversationProvider()
    agent_client.app.state.conversation_provider = provider
    conversation = agent_client.post("/api/agent/conversations").json()

    first = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "我要同步学生"},
    )
    assert first.status_code == 200, first.text

    second = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "继续选择数据来源"},
    )
    assert second.status_code == 200, second.text
    current = agent_client.get("/api/agent/conversations/current")
    assert current.json()["intent"]["entity_types"] == ["student"]
    assert current.json()["intent"]["title"] == "学生同步"

    evidence = json.loads(provider.requests[1].messages[1].content)["untrusted_evidence"]
    assert evidence["current_intent"]["entity_types"] == ["student"]
    assert evidence["current_intent"]["title"] == "学生同步"
    assert evidence["history"] == [
        {
            "role": "user",
            "kind": "normal",
            "text": "我要同步学生",
        },
        {
            "role": "assistant",
            "kind": "normal",
            "text": "已记住要同步学生，请继续选择数据来源。",
        },
        {
            "role": "user",
            "kind": "normal",
            "text": "继续选择数据来源",
        },
    ]


def test_conversation_context_limit_preserves_user_message_without_calling_model(
    agent_client: TestClient,
) -> None:
    provider = ConversationProvider()
    agent_client.app.state.conversation_provider = provider
    agent_client.app.state.settings.conversation_context_max_tokens = 100
    agent_client.app.state.settings.conversation_context_reserved_output_tokens = 20
    conversation = agent_client.post("/api/agent/conversations").json()

    response = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "我要同步学生"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "conversation_context_limit"
    assert response.json()["detail"]["message"] == (
        "当前对话内容已达到模型处理上限，请开启新对话"
    )
    assert provider.requests == []
    current = agent_client.get("/api/agent/conversations/current")
    assert [
        (item["role"], item["kind"], item["text"])
        for item in current.json()["messages"]
    ] == [("user", "normal", "我要同步学生")]


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

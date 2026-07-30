import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.ai.providers.base import LLMRequest, LLMResponse
from app.api_connectors.registry import ProviderRegistry
from app.main import create_app
from tests.integration.agent_runtime.test_api_task_binding import (
    MANIFEST,
    AdapterMustNotRun,
    _seed_connection,
    _settings,
)


class ApiConversationProvider:
    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        evidence = json.loads(request.messages[1].content)["untrusted_evidence"]
        connections = evidence["available_api_connections"]
        result: dict[str, object]
        if not connections:
            result = {
                "kind": "api_configuration",
                "api_provider_id": MANIFEST.provider_id,
                "message_zh": "需要先安全配置组织 API 连接。",
            }
        else:
            result = {
                "kind": "start_confirmation",
                "title": "API 教师同步",
                "entity_types": ["teacher"],
                "source_api_connection_id": connections[0]["connection_id"],
                "target_configuration_id": "seewo-mysql",
                "message_zh": "已确认 API 只读权威来源和 MySQL 希沃目标。",
            }
        return LLMResponse(output={"result": result}, provider="stub", model="stub")


@pytest.fixture
def api_conversation_client(tmp_path: Path):
    key = Fernet.generate_key()
    settings = _settings(key).model_copy(
        update={
            "database_url": (
                f"sqlite+aiosqlite:///{tmp_path / 'api-conversation.db'}"
            ),
            "upload_root": tmp_path / "uploads",
            "snapshot_root": tmp_path / "snapshots",
            "quarantine_root": tmp_path / "quarantine",
            "auto_create_schema": True,
            "demo_tenant_id": "school-1",
            "demo_operator_id": "operator-1",
        }
    )
    adapter = AdapterMustNotRun()
    registry = ProviderRegistry()
    registry.register(MANIFEST, adapter)
    with TestClient(create_app(settings)) as client:
        client.app.state.api_provider_registry = registry
        client.app.state.conversation_provider = ApiConversationProvider()
        yield client, key, adapter


def _conversation(client: TestClient) -> str:
    response = client.post("/api/agent/conversations")
    assert response.status_code == 201
    return response.json()["id"]


def test_conversation_returns_safe_configuration_card_without_secret_values(
    api_conversation_client,
) -> None:
    client, _key, _adapter = api_conversation_client

    response = client.post(
        f"/api/agent/conversations/{_conversation(client)}/messages",
        json={"message": "同步组织 API 里的老师"},
    )

    assert response.status_code == 200, response.text
    card = response.json()["api_connection"]
    assert card == {
        "provider_id": MANIFEST.provider_id,
        "state": "configuration_required",
        "required_secret_fields": ["client_id", "client_secret"],
        "capabilities": {},
        "visibility_summary": {},
    }
    assert "public_configuration" not in response.json()["api_connection"]


def test_confirmed_api_connection_creates_one_idempotent_graph_task(
    api_conversation_client,
) -> None:
    client, key, adapter = api_conversation_client

    async def seed():
        async with client.app.state.database.session_factory() as session:
            async with session.begin():
                return await _seed_connection(session, fernet_key=key)

    connection = client.portal.call(seed)
    conversation_id = _conversation(client)
    message = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"message": "把组织 API 的老师同步到希沃 MySQL"},
    )
    assert message.status_code == 200, message.text
    payload = message.json()
    assert payload["intent"]["source"] == {
        "kind": "api",
        "configuration_id": str(connection.id),
    }
    assert payload["start_confirmation"] is not None

    headers = {
        "Idempotency-Key": "api-conversation-task-1",
        "X-Accept-Current-Target-Baseline": "false",
    }
    first = client.post(
        f"/api/agent/conversations/{conversation_id}/tasks",
        headers=headers,
        json=payload["intent"],
    )
    replay = client.post(
        f"/api/agent/conversations/{conversation_id}/tasks",
        headers=headers,
        json=payload["intent"],
    )

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["workflow_version"] == "agent-graph-v1"
    assert adapter.calls == 0

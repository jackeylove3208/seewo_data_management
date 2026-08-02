import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api.dependencies import get_operator_context
from app.api_connectors.contracts import (
    ApiProviderError,
    CapturedApiPage,
    ConnectionTestResult,
    ProviderManifest,
)
from app.api_connectors.registry import ProviderRegistry
from app.core.security import OperatorContext
from app.main import create_app
from app.models.agent_runtime import AgentConversationRecord
from app.models.api_connectors import ApiConnectionRecord
from app.schemas.agent_ingestion import AgentEntityKind
from tests.integration.repositories.test_agent_external_identity import (
    _seed_context,
)
from tests.settings import build_test_settings

MANIFEST = ProviderManifest(
    provider_id="dingtalk",
    manifest_version="1.0.0",
    adapter_version="1.0.0",
    supported_entities=frozenset(
        {
            AgentEntityKind.DEPARTMENT,
            AgentEntityKind.STUDENT,
            AgentEntityKind.TEACHER,
        }
    ),
    required_secret_fields=("app_key", "app_secret"),
    required_capabilities=("organization.read",),
    endpoint_hosts=("api.dingtalk.com",),
    maximum_pages=100,
    projection_version="organization-six-fields-v1",
)


class FakeDingTalkAdapter:
    manifest = MANIFEST

    def __init__(self) -> None:
        self.secrets_seen: list[dict[str, str]] = []
        self.result = ConnectionTestResult(
            eligible=True,
            capabilities={"organization.read": True},
            visibility_summary={"visible": True, "record_count": 15},
        )
        self.safe_error_code: str | None = None

    async def test_connection(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
    ) -> ConnectionTestResult:
        del public_configuration
        self.secrets_seen.append(dict(secret))
        if self.safe_error_code is not None:
            raise ApiProviderError(self.safe_error_code)
        return self.result

    async def capture(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        selected_entities: frozenset[AgentEntityKind],
    ) -> AsyncIterator[CapturedApiPage]:
        del public_configuration, secret, selected_entities
        if False:
            yield CapturedApiPage(page_number=1, records=(), next_cursor=None)

@pytest.fixture
def connector_client(tmp_path: Path):
    key = Fernet.generate_key().decode()
    settings = build_test_settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api-connectors.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        auto_create_schema=True,
        new_agent_enabled=True,
        new_agent_analysis_only=False,
        new_agent_api_connector_enabled=True,
        demo_operator_id="operator-1",
        api_connector_secret_key=key,
        api_connector_configurations={
            "legacy-placeholder": {
                "credential_reference": "secret://legacy/placeholder",
                "endpoint": "https://connector.example.test/v1/people",
                "record_id_field": "id",
                "version_field": "etag",
            }
        },
        database_connector_configurations={
            "seewo-data-mysql": {
                "credential_reference": "secret://connectors/seewo-data-mysql",
                "dialect": "mysql",
                "table_name": "organization_people",
                "primary_key": "id",
                "version_column": "row_version",
                "field_columns": {
                    "category": "category",
                    "name": "name",
                    "number": "number",
                    "class_name": "class_name",
                    "phone": "phone",
                    "email": "email",
                },
                "source_role": "target",
                "capabilities": {
                    "read": True,
                    "paginated": True,
                    "create": True,
                    "update": True,
                    "delete": True,
                    "optimistic_version": True,
                    "read_after_write": True,
                },
            }
        },
        database_connector_credentials={
            "secret://connectors/seewo-data-mysql": "mysql+asyncmy://hidden"
        },
    )
    adapter = FakeDingTalkAdapter()
    registry = ProviderRegistry()
    registry.register(MANIFEST, adapter)
    with TestClient(create_app(settings)) as test_client:
        test_client.app.state.api_provider_registry = registry
        yield test_client, adapter


def _create_connection(client: TestClient) -> dict[str, object]:
    configuration_session = client.post(
        "/api/connectors/configuration-sessions",
        json={"provider_id": "dingtalk"},
    )
    assert configuration_session.status_code == 201
    response = client.post(
        "/api/connectors/connections",
        json={
            "configuration_session_id": configuration_session.json()["id"],
            "provider_id": "dingtalk",
            "display_name": "学校钉钉",
            "public_configuration": {"organization_ref": "school-1"},
            "secret": {"app_key": "app", "app_secret": "secret"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_conversation_configuration_creates_a_task_ephemeral_connection(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client
    conversation = client.post("/api/agent/conversations").json()
    configuration_session = client.post(
        "/api/connectors/configuration-sessions",
        json={
            "provider_id": "dingtalk",
            "conversation_id": conversation["id"],
        },
    )
    assert configuration_session.status_code == 201, configuration_session.text

    created = client.post(
        "/api/connectors/connections",
        json={
            "configuration_session_id": configuration_session.json()["id"],
            "provider_id": "dingtalk",
            "display_name": "钉钉临时连接-测试",
            "public_configuration": {
                "person_entity_kind": "student",
                "root_department_id": 42,
                "number_field": "student_number",
                "class_name_field": "class_name",
            },
            "secret": {"app_key": "app", "app_secret": "secret"},
        },
    )

    assert created.status_code == 201, created.text

    async def inspect_connection():
        async with client.app.state.database.session_factory() as session:
            return await session.get(ApiConnectionRecord, UUID(created.json()["id"]))

    record = client.portal.call(inspect_connection)
    assert record is not None
    assert record.scope == "task_ephemeral"
    assert str(record.conversation_id) == conversation["id"]
    assert record.task_id is None
    assert record.credentials_revoked_at is None
    current = client.get("/api/agent/conversations/current")
    assert current.status_code == 200, current.text
    assert current.json()["intent"]["entity_types"] == ["student"]


def test_successful_conversation_connection_test_exposes_start_confirmation(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, adapter = connector_client
    conversation = client.post("/api/agent/conversations").json()
    configuration_session = client.post(
        "/api/connectors/configuration-sessions",
        json={
            "provider_id": "dingtalk",
            "conversation_id": conversation["id"],
        },
    ).json()
    created = client.post(
        "/api/connectors/connections",
        json={
            "configuration_session_id": configuration_session["id"],
            "provider_id": "dingtalk",
            "display_name": "钉钉学生连接",
            "public_configuration": {
                "person_entity_kind": "student",
                "root_department_id": 42,
            },
            "secret": {"app_key": "app", "app_secret": "secret"},
        },
    )
    assert created.status_code == 201, created.text

    adapter.result = ConnectionTestResult(
        eligible=False,
        capabilities={},
        visibility_summary={"visible": False, "student_count": 0},
        safe_error_code="connector_visibility_empty",
    )
    failed_test = client.post(
        f"/api/connectors/connections/{created.json()['id']}/test",
        json={"conversation_id": conversation["id"]},
    )
    assert failed_test.status_code == 200, failed_test.text
    assert failed_test.json()["state"] == "invalid"
    assert client.get("/api/agent/conversations/current").json()[
        "start_confirmation"
    ] is None

    rotated = client.post(
        f"/api/connectors/connections/{created.json()['id']}/rotate-secret",
        json={
            "conversation_id": conversation["id"],
            "public_configuration": {
                "person_entity_kind": "teacher",
                "root_department_id": 42,
            },
            "secret": {"app_key": "app-2", "app_secret": "secret-2"},
        },
    )
    assert rotated.status_code == 200, rotated.text
    adapter.result = ConnectionTestResult(
        eligible=True,
        capabilities={
            "organization.read": True,
            "entity.teacher.read": True,
        },
        visibility_summary={
            "visible": True,
            "record_count": 5,
            "teacher_count": 5,
        },
    )
    tested = client.post(
        f"/api/connectors/connections/{created.json()['id']}/test",
        json={"conversation_id": conversation["id"]},
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["state"] == "active"

    current = client.get("/api/agent/conversations/current")
    assert current.status_code == 200, current.text
    assert current.json()["start_confirmation"] == {
        "title": "钉钉教师同步",
        "summary": "钉钉教师同步",
        "entity_types": ["teacher"],
    }


def test_conversation_configuration_preserves_an_existing_explicit_target(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client
    conversation = client.post("/api/agent/conversations").json()

    async def select_explicit_target() -> None:
        async with client.app.state.database.session_factory() as session:
            async with session.begin():
                record = await session.get(
                    AgentConversationRecord,
                    UUID(conversation["id"]),
                )
                assert record is not None
                record.context = {
                    "target": {
                        "kind": "database",
                        "configuration_id": "seewo-archive-mysql",
                    }
                }

    client.portal.call(select_explicit_target)
    configuration_session = client.post(
        "/api/connectors/configuration-sessions",
        json={
            "provider_id": "dingtalk",
            "conversation_id": conversation["id"],
        },
    ).json()
    created = client.post(
        "/api/connectors/connections",
        json={
            "configuration_session_id": configuration_session["id"],
            "provider_id": "dingtalk",
            "display_name": "钉钉学生连接",
            "public_configuration": {
                "person_entity_kind": "student",
                "root_department_id": 42,
            },
            "secret": {"app_key": "app", "app_secret": "secret"},
        },
    )

    assert created.status_code == 201, created.text
    current = client.get("/api/agent/conversations/current")
    target = current.json()["intent"]["target"]
    assert target["kind"] == "database"
    assert target["configuration_id"] == "seewo-archive-mysql"


def test_conversation_configuration_requires_fresh_dingtalk_scope_fields(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client
    conversation = client.post("/api/agent/conversations").json()
    configuration_session = client.post(
        "/api/connectors/configuration-sessions",
        json={
            "provider_id": "dingtalk",
            "conversation_id": conversation["id"],
        },
    ).json()

    created = client.post(
        "/api/connectors/connections",
        json={
            "configuration_session_id": configuration_session["id"],
            "provider_id": "dingtalk",
            "display_name": "缺少范围配置",
            "public_configuration": {},
            "secret": {"app_key": "app", "app_secret": "secret"},
        },
    )

    assert created.status_code == 422
    assert "personnel type" in created.json()["detail"]


def test_new_conversation_connection_supersedes_previous_unbound_connection(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client
    conversation = client.post("/api/agent/conversations").json()

    def create(display_name: str) -> dict[str, object]:
        configuration_session = client.post(
            "/api/connectors/configuration-sessions",
            json={
                "provider_id": "dingtalk",
                "conversation_id": conversation["id"],
            },
        ).json()
        response = client.post(
            "/api/connectors/connections",
            json={
                "configuration_session_id": configuration_session["id"],
                "provider_id": "dingtalk",
                "display_name": display_name,
                "public_configuration": {
                    "person_entity_kind": "teacher",
                    "root_department_id": 1,
                },
                "secret": {"app_key": "app", "app_secret": "secret"},
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    previous = create("钉钉临时连接-旧")
    current = create("钉钉临时连接-新")

    async def inspect_connections():
        async with client.app.state.database.session_factory() as session:
            return (
                await session.get(ApiConnectionRecord, UUID(previous["id"])),
                await session.get(ApiConnectionRecord, UUID(current["id"])),
            )

    old_record, current_record = client.portal.call(inspect_connections)
    assert old_record is not None
    assert old_record.state == "disabled"
    assert old_record.disabled_reason == "superseded"
    assert old_record.credentials_revoked_at is not None
    assert current_record is not None
    assert current_record.credentials_revoked_at is None


def test_ephemeral_connection_rotation_requires_its_conversation(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client
    conversation = client.post("/api/agent/conversations").json()
    configuration_session = client.post(
        "/api/connectors/configuration-sessions",
        json={
            "provider_id": "dingtalk",
            "conversation_id": conversation["id"],
        },
    ).json()
    created = client.post(
        "/api/connectors/connections",
        json={
            "configuration_session_id": configuration_session["id"],
            "provider_id": "dingtalk",
            "display_name": "不可跨对话轮换",
            "public_configuration": {
                "person_entity_kind": "teacher",
                "root_department_id": 1,
            },
            "secret": {"app_key": "app", "app_secret": "secret"},
        },
    ).json()

    rotated = client.post(
        f"/api/connectors/connections/{created['id']}/rotate-secret",
        json={
            "conversation_id": "00000000-0000-0000-0000-000000000001",
            "public_configuration": {
                "person_entity_kind": "teacher",
                "root_department_id": 1,
            },
            "secret": {"app_key": "new", "app_secret": "new-secret"},
        },
    )

    assert rotated.status_code == 409


def test_ephemeral_connection_test_requires_its_active_conversation_and_ttl(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client
    conversation = client.post("/api/agent/conversations").json()
    configuration_session = client.post(
        "/api/connectors/configuration-sessions",
        json={
            "provider_id": "dingtalk",
            "conversation_id": conversation["id"],
        },
    ).json()
    created = client.post(
        "/api/connectors/connections",
        json={
            "configuration_session_id": configuration_session["id"],
            "provider_id": "dingtalk",
            "display_name": "需要对话校验的连接",
            "public_configuration": {
                "person_entity_kind": "teacher",
                "root_department_id": 1,
            },
            "secret": {"app_key": "app", "app_secret": "secret"},
        },
    ).json()

    without_conversation = client.post(
        f"/api/connectors/connections/{created['id']}/test",
        json={},
    )
    assert without_conversation.status_code == 409

    valid = client.post(
        f"/api/connectors/connections/{created['id']}/test",
        json={"conversation_id": conversation["id"]},
    )
    assert valid.status_code == 200, valid.text

    async def expire_connection():
        async with client.app.state.database.session_factory() as session:
            async with session.begin():
                record = await session.get(
                    ApiConnectionRecord,
                    UUID(created["id"]),
                )
                assert record is not None
                record.created_at = datetime.now(UTC) - timedelta(hours=25)

    client.portal.call(expire_connection)
    expired = client.post(
        f"/api/connectors/connections/{created['id']}/test",
        json={"conversation_id": conversation["id"]},
    )
    assert expired.status_code == 409


def test_conversation_reset_revokes_an_unbound_ephemeral_connection(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client
    conversation = client.post("/api/agent/conversations").json()
    configuration_session = client.post(
        "/api/connectors/configuration-sessions",
        json={
            "provider_id": "dingtalk",
            "conversation_id": conversation["id"],
        },
    ).json()
    created = client.post(
        "/api/connectors/connections",
        json={
            "configuration_session_id": configuration_session["id"],
            "provider_id": "dingtalk",
            "display_name": "即将撤销的临时连接",
            "public_configuration": {
                "person_entity_kind": "teacher",
                "root_department_id": 1,
            },
            "secret": {"app_key": "app", "app_secret": "secret"},
        },
    )
    assert created.status_code == 201, created.text

    reset = client.post(
        "/api/agent/conversations/current/reset",
        headers={"Idempotency-Key": "reset-ephemeral-connection"},
        json={},
    )
    assert reset.status_code == 201, reset.text

    async def inspect_connection():
        async with client.app.state.database.session_factory() as session:
            return await session.get(ApiConnectionRecord, UUID(created.json()["id"]))

    record = client.portal.call(inspect_connection)
    assert record is not None
    assert record.state == "disabled"
    assert record.disabled_reason == "conversation_reset"
    assert record.credentials_revoked_at is not None
    assert record.conversation_id is None


def test_connection_lifecycle_never_returns_secret(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, adapter = connector_client

    created = _create_connection(client)

    serialized = json.dumps(created).lower()
    assert '"app"' not in serialized
    assert "secret" not in serialized
    assert created["state"] == "pending"
    connection_id = created["id"]

    listed = client.get("/api/connectors/connections")
    assert listed.status_code == 200
    assert listed.json() == [created]
    fetched = client.get(f"/api/connectors/connections/{connection_id}")
    assert fetched.status_code == 200
    assert fetched.json() == created

    tested = client.post(f"/api/connectors/connections/{connection_id}/test")
    assert tested.status_code == 200, tested.text
    assert tested.json()["state"] == "active"
    assert tested.json()["capabilities"] == {"organization.read": True}
    assert tested.json()["visibility_summary"]["record_count"] == 15
    assert adapter.secrets_seen == [{"app_key": "app", "app_secret": "secret"}]
    assert "secret" not in json.dumps(tested.json()).lower()

    rotated = client.post(
        f"/api/connectors/connections/{connection_id}/rotate-secret",
        json={
            "public_configuration": {
                "organization_ref": "school-2",
                "person_entity_kind": "student",
            },
            "secret": {"app_key": "new-app", "app_secret": "new-secret"},
        },
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["state"] == "pending"
    assert rotated.json()["public_configuration"] == {
        "organization_ref": "school-2",
        "person_entity_kind": "student",
    }
    assert "secret" not in json.dumps(rotated.json()).lower()

    retested = client.post(f"/api/connectors/connections/{connection_id}/test")
    assert retested.status_code == 200
    assert adapter.secrets_seen[-1] == {
        "app_key": "new-app",
        "app_secret": "new-secret",
    }

    deleted = client.delete(f"/api/connectors/connections/{connection_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/connectors/connections/{connection_id}").status_code == 404


def test_connection_queries_are_tenant_scoped(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client
    connection_id = _create_connection(client)["id"]
    client.app.dependency_overrides[get_operator_context] = lambda: OperatorContext(
        operator_id="operator-2",
        tenant_id="school-2",
    )
    try:
        assert client.get("/api/connectors/connections").json() == []
        assert client.get(f"/api/connectors/connections/{connection_id}").status_code == 404
        assert (
            client.post(f"/api/connectors/connections/{connection_id}/test").status_code
            == 404
        )
        assert (
            client.post(
                f"/api/connectors/connections/{connection_id}/rotate-secret",
                json={"secret": {"app_key": "x", "app_secret": "y"}},
            ).status_code
            == 404
        )
        assert client.delete(f"/api/connectors/connections/{connection_id}").status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_operator_context, None)


def test_provider_and_configuration_session_views_are_metadata_only(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client

    providers = client.get("/api/connectors/providers")
    assert providers.status_code == 200
    assert providers.json()[0]["provider_id"] == "dingtalk"
    assert providers.json()[0]["required_secret_fields"] == ["app_key", "app_secret"]

    configuration_session = client.post(
        "/api/connectors/configuration-sessions",
        json={"provider_id": "dingtalk"},
    )
    assert configuration_session.status_code == 201
    assert configuration_session.json()["provider_id"] == "dingtalk"
    assert configuration_session.json()["required_secret_fields"] == [
        "app_key",
        "app_secret",
    ]


def test_connection_rejects_unknown_provider_and_secret_shape(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client

    unknown = client.post(
        "/api/connectors/connections",
        json={
            "configuration_session_id": "00000000-0000-0000-0000-000000000001",
            "provider_id": "unknown",
            "display_name": "未知连接",
            "public_configuration": {},
            "secret": {"key": "value"},
        },
    )
    assert unknown.status_code == 422

    configuration_session = client.post(
        "/api/connectors/configuration-sessions",
        json={"provider_id": "dingtalk"},
    ).json()
    invalid_secret = client.post(
        "/api/connectors/connections",
        json={
            "configuration_session_id": configuration_session["id"],
            "provider_id": "dingtalk",
            "display_name": "学校钉钉",
            "public_configuration": {},
            "secret": {"app_key": "only-one-field"},
        },
    )
    assert invalid_secret.status_code == 422


def test_connection_test_persists_only_sanitized_provider_failure(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, adapter = connector_client
    connection_id = _create_connection(client)["id"]
    adapter.safe_error_code = "connector_permission_denied"

    response = client.post(f"/api/connectors/connections/{connection_id}/test")

    assert response.status_code == 200
    assert response.json()["state"] == "invalid"
    assert response.json()["last_safe_error_code"] == "connector_permission_denied"
    assert response.json()["capabilities"] == {}
    assert response.json()["visibility_summary"] == {}
    assert "provider response body" not in response.text


def test_empty_visibility_is_not_eligible_for_synchronization(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, adapter = connector_client
    connection_id = _create_connection(client)["id"]
    adapter.result = ConnectionTestResult(
        eligible=False,
        capabilities={"organization.read": True},
        visibility_summary={"visible": False, "record_count": 0},
        safe_error_code="connector_visibility_empty",
    )

    response = client.post(f"/api/connectors/connections/{connection_id}/test")

    assert response.status_code == 200
    assert response.json()["state"] == "invalid"
    assert response.json()["last_safe_error_code"] == "connector_visibility_empty"


def test_configuration_session_is_transactional_and_consumed_only_after_success(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client
    created = _create_connection(client)
    configuration_session = client.post(
        "/api/connectors/configuration-sessions",
        json={"provider_id": "dingtalk"},
    ).json()
    payload = {
        "configuration_session_id": configuration_session["id"],
        "provider_id": "dingtalk",
        "display_name": created["display_name"],
        "public_configuration": {},
        "secret": {"app_key": "app-2", "app_secret": "secret-2"},
    }

    assert client.post("/api/connectors/connections", json=payload).status_code == 409

    payload["display_name"] = "另一个连接"
    replay = client.post("/api/connectors/connections", json=payload)
    assert replay.status_code == 201

    payload["display_name"] = "第三个连接"
    consumed = client.post("/api/connectors/connections", json=payload)
    assert consumed.status_code == 422
    assert "session" in consumed.json()["detail"]


def test_external_identity_binding_endpoints_are_audited_and_tenant_scoped(
    connector_client: tuple[TestClient, FakeDingTalkAdapter],
) -> None:
    client, _adapter = connector_client

    async def seed():
        async with client.app.state.database.session_factory() as session:
            async with session.begin():
                _task, run, connection, authority, targets = await _seed_context(
                    session
                )
                return (
                    run.id,
                    connection.id,
                    authority.stable_locator,
                    targets[0].stable_locator,
                )

    run_id, connection_id, authority_locator, target_locator = client.portal.call(
        seed
    )
    response = client.post(
        "/api/agent/external-identity-bindings",
        json={
            "run_id": str(run_id),
            "connection_id": str(connection_id),
            "entity_kind": "teacher",
            "authority_stable_locator": authority_locator,
            "target_connector_id": "seewo-mysql",
            "target_stable_locator": target_locator,
        },
    )

    assert response.status_code == 201, response.text
    binding = response.json()
    assert binding["status"] == "active"
    assert binding["confirmed_by"] == "operator-1"
    assert binding["binding_version"] == 1
    assert "phone" not in json.dumps(binding).lower()
    assert client.get("/api/agent/external-identity-bindings").json() == [
        binding
    ]

    client.app.dependency_overrides[get_operator_context] = lambda: OperatorContext(
        operator_id="operator-2",
        tenant_id="school-2",
    )
    try:
        assert client.get("/api/agent/external-identity-bindings").json() == []
        assert (
            client.post(
                f"/api/agent/external-identity-bindings/{binding['id']}/revoke"
            ).status_code
            == 404
        )
    finally:
        client.app.dependency_overrides.pop(get_operator_context, None)

    revoked = client.post(
        f"/api/agent/external-identity-bindings/{binding['id']}/revoke"
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["revoked_by"] == "operator-1"

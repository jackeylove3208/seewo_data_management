from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.models.api_connectors import (
    AgentExternalIdentityBindingRecord,
    AgentSourceBindingRecord,
    ApiAuthoritySourceRecord,
    ApiConfigurationSessionRecord,
    ApiConnectionRecord,
)


def test_api_connection_contains_only_a_secret_reference() -> None:
    record = ApiConnectionRecord(
        tenant_id="school-1",
        provider_id="dingtalk",
        display_name="钉钉通讯录",
        public_configuration={"organization_ref": "school-1"},
        secret_ref="db-secret:00000000-0000-0000-0000-000000000001",
        manifest_version="1.0.0",
        adapter_version="1.0.0",
        capabilities={"department": True, "teacher": True},
        visibility_summary={"visible": True, "record_count": 10},
        state="active",
        last_tested_at=datetime.now(UTC),
        last_safe_error_code=None,
        created_by="operator-1",
        updated_by="operator-1",
    )

    assert "secret" not in record.public_configuration
    assert record.secret_ref.startswith("db-secret:")
    assert ApiConnectionRecord.__table__.c.state.default.arg == "pending"


def test_api_authority_source_is_unique_per_task() -> None:
    assert _constraint_names(ApiAuthoritySourceRecord) >= {
        "uq_api_authority_sources_task_id",
        "uq_api_authority_sources_source_file_id",
        "uq_api_authority_sources_snapshot_id",
    }


def test_external_binding_is_unique_per_authority_and_target_locator() -> None:
    assert _constraint_names(AgentExternalIdentityBindingRecord) >= {
        "uq_agent_external_binding_authority",
        "uq_agent_external_binding_target",
    }

    record = AgentExternalIdentityBindingRecord(
        tenant_id="school-1",
        provider_id="dingtalk",
        connection_id=uuid4(),
        entity_kind="teacher",
        authority_stable_locator="api:connection-1:teacher:user-42",
        target_connector_id="seewo-mysql",
        target_stable_locator="database:seewo-mysql:teacher-9",
        status="active",
        binding_version=1,
        confirmed_by="operator-1",
        confirmed_at=datetime.now(UTC),
        revoked_by=None,
        revoked_at=None,
        evidence_hash="a" * 64,
    )
    assert isinstance(record.id, UUID) or record.id is None
    assert record.status == "active"


def test_source_bindings_are_unique_per_task_role() -> None:
    assert _constraint_names(AgentSourceBindingRecord) >= {
        "uq_agent_source_bindings_task_role",
    }


def test_configuration_session_tracks_single_use_state() -> None:
    record = ApiConfigurationSessionRecord(
        tenant_id="school-1",
        provider_id="dingtalk",
        expires_at=datetime.now(UTC),
        consumed_at=None,
    )

    assert record.consumed_at is None


def _constraint_names(model: type[Any]) -> set[str]:
    table = model.__table__
    return {
        constraint.name
        for constraint in (*table.constraints, *table.indexes)
        if constraint.name is not None
    }

from types import SimpleNamespace

import pytest

from app.agent_runtime.source_bindings import resolve_source_bindings


def test_api_database_roles_are_resolved_independently() -> None:
    task = SimpleNamespace(
        agent_intent={
            "source": {"kind": "api", "configuration_id": "ding-school"},
            "target": {"kind": "database", "configuration_id": "seewo-mysql"},
        }
    )

    authority, target = resolve_source_bindings(task)

    assert authority.role == "authoritative"
    assert authority.connector_kind == "api"
    assert authority.configuration_id == "ding-school"
    assert authority.mapping_checkpoint_key == "graph-api-field-mapping-v3:authoritative"
    assert authority.normalization_checkpoint_key == (
        "graph-source-normalization-v3:authoritative"
    )

    assert target.role == "target"
    assert target.connector_kind == "database"
    assert target.configuration_id == "seewo-mysql"
    assert target.mapping_checkpoint_key == "graph-database-field-mapping-v3:target"
    assert target.normalization_checkpoint_key == "graph-source-normalization-v3:target"


def test_role_binding_rejects_a_missing_selection() -> None:
    task = SimpleNamespace(
        agent_intent={
            "source": {"kind": "api", "configuration_id": "ding-school"},
        }
    )

    with pytest.raises(ValueError, match="target selection is missing"):
        resolve_source_bindings(task)

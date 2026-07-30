from uuid import uuid4

import pytest

from app.agent_runtime.source_bindings import (
    _binding_from_record,
    _configuration_fingerprint,
)
from app.models.api_connectors import AgentSourceBindingRecord


def test_api_database_roles_are_resolved_independently() -> None:
    record = AgentSourceBindingRecord(
        tenant_id="school-1",
        task_id=uuid4(),
        role="target",
        connector_kind="database",
        configuration_id="seewo-mysql",
        snapshot_id=uuid4(),
        configuration_fingerprint=_configuration_fingerprint({}),
        frozen_public_configuration={},
        credential_reference="secret://seewo-mysql",
        mapping_checkpoint_key="graph-database-field-mapping-v3:target",
        normalization_checkpoint_key="graph-source-normalization-v3:target",
    )

    target = _binding_from_record(record)

    assert target.role == "target"
    assert target.connector_kind == "database"
    assert target.configuration_id == "seewo-mysql"
    assert target.mapping_checkpoint_key == "graph-database-field-mapping-v3:target"
    assert target.normalization_checkpoint_key == "graph-source-normalization-v3:target"


def test_role_binding_rejects_a_changed_frozen_configuration() -> None:
    record = AgentSourceBindingRecord(
        tenant_id="school-1",
        task_id=uuid4(),
        role="target",
        connector_kind="database",
        configuration_id="seewo-mysql",
        snapshot_id=uuid4(),
        configuration_fingerprint="0" * 64,
        frozen_public_configuration={"table_name": "changed"},
        credential_reference="secret://seewo-mysql",
        mapping_checkpoint_key="graph-database-field-mapping-v3:target",
        normalization_checkpoint_key="graph-source-normalization-v3:target",
    )

    with pytest.raises(ValueError, match="fingerprint changed"):
        _binding_from_record(record)

from uuid import uuid4

import pytest

from app.connectors.configured import (
    ConfiguredApiConnector,
    ConnectorCapabilities,
    DatabaseConnectorConfiguration,
    InMemoryConnectorStore,
)
from app.ingestion.agent_database_adapter import AgentDatabaseIngestionAdapter
from app.schemas.agent_ingestion import AgentEntityKind, AgentSourceRole


def _connector(
    *,
    role: str,
    records: list[dict[str, object]],
) -> ConfiguredApiConnector:
    configuration = DatabaseConnectorConfiguration(
        credential_reference=f"secret://connectors/{role}",
        dialect="postgresql" if role == "authoritative" else "mysql",
        table_name="organization_people",
        primary_key="id",
        version_column="row_version",
        field_columns={
            "category": "entity_type",
            "name": "full_name",
            "number": "person_code",
            "class_name": "class_label",
            "phone": "mobile",
            "email": "mail",
        },
        source_role=role,
        capabilities=ConnectorCapabilities(read=True, paginated=True),
    )
    return ConfiguredApiConnector(
        configuration=configuration,
        store=InMemoryConnectorStore(records=records),
    )


@pytest.mark.asyncio
async def test_database_adapter_projects_fixed_fields_without_model_calls() -> None:
    connector = _connector(
        role="authoritative",
        records=[
            {
                "id": "student-row-1",
                "row_version": "v1",
                "entity_type": "学生",
                "full_name": " 张三 ",
                "person_code": "S001",
                "class_label": "一班",
                "mobile": "138 0000 0001",
                "mail": "STUDENT@EXAMPLE.TEST",
            }
        ],
    )

    outcome = await AgentDatabaseIngestionAdapter().extract(
        connector=connector,
        connector_id="authority-postgres",
        task_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        source_role=AgentSourceRole.AUTHORITATIVE,
        selected_entities=frozenset({AgentEntityKind.STUDENT}),
    )

    assert len(outcome.records) == 1
    record = outcome.records[0]
    assert record.stable_locator == ("database:authority-postgres:student-row-1")
    assert record.stable_order == 1
    assert record.name == "张三"
    assert record.phone == "13800000001"
    assert record.email == "student@example.test"
    assert outcome.marks == ()


@pytest.mark.asyncio
async def test_database_adapter_marks_authority_row_missing_required_field() -> None:
    connector = _connector(
        role="authoritative",
        records=[
            {
                "id": "teacher-row-1",
                "row_version": "v1",
                "entity_type": "教师",
                "full_name": "李老师",
                "person_code": "T001",
                "class_label": None,
                "mobile": None,
                "mail": "teacher@example.test",
            }
        ],
    )

    outcome = await AgentDatabaseIngestionAdapter().extract(
        connector=connector,
        connector_id="authority-postgres",
        task_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        source_role=AgentSourceRole.AUTHORITATIVE,
        selected_entities=frozenset({AgentEntityKind.TEACHER}),
    )

    assert len(outcome.records) == 1
    assert outcome.marks[0].reason_code == "authority_required_fields_missing"
    assert outcome.marks[0].affected_fields == ("phone",)


@pytest.mark.asyncio
async def test_database_adapter_uses_frozen_mapping_instead_of_connector_defaults() -> None:
    connector = _connector(
        role="authoritative",
        records=[
            {
                "id": "student-row-1",
                "row_version": "v1",
                "entity_type": "学生",
                "full_name": "张三",
                "person_code": "S001",
                "class_label": "一班",
                "mobile": "13800000001",
                "mail": "student@example.test",
            }
        ],
    )

    outcome = await AgentDatabaseIngestionAdapter().extract(
        connector=connector,
        connector_id="authority-postgres",
        task_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        source_role=AgentSourceRole.AUTHORITATIVE,
        selected_entities=frozenset({AgentEntityKind.STUDENT}),
        field_mapping={
            "category": "entity_type",
            "name": "full_name",
            "number": "person_code",
            "class_name": "class_label",
            "phone": "mobile",
            "email": "mail",
        },
    )

    assert outcome.records[0].number == "S001"

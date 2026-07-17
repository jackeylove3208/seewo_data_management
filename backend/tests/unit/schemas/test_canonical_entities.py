from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.canonical_entities import (
    CanonicalEntity,
    ClassEntity,
    EntityType,
    Membership,
    OrganizationUnit,
    SourceRole,
    Student,
    Teacher,
)
from app.schemas.ingestion import SnapshotMode, SnapshotScope


def provenance() -> dict[str, object]:
    return {
        "tenant_id": "school-1",
        "snapshot_id": uuid4(),
        "source_role": SourceRole.AUTHORITATIVE,
        "source_id": "source-1",
        "raw_row_number": 2,
        "raw_payload": {"id": "source-1"},
    }


@pytest.mark.parametrize(
    ("entity", "expected_type"),
    [
        (OrganizationUnit(**provenance(), name="教务处", code="D01"), EntityType.ORGANIZATION_UNIT),
        (ClassEntity(**provenance(), name="高一(1)班", grade="高一"), EntityType.CLASS),
        (Teacher(**provenance(), name="张三", department_source_id="D01"), EntityType.TEACHER),
        (Student(**provenance(), name="李四", class_source_id="C01"), EntityType.STUDENT),
        (
            Membership(
                **provenance(),
                member_source_id="T001",
                container_source_id="D01",
                role="member",
            ),
            EntityType.MEMBERSHIP,
        ),
    ],
)
def test_canonical_entity_keeps_type_and_provenance(
    entity: CanonicalEntity,
    expected_type: EntityType,
) -> None:
    assert entity.entity_type is expected_type
    assert entity.raw_row_number == 2
    assert entity.raw_payload == {"id": "source-1"}


def test_discriminated_union_parses_entity_payload() -> None:
    payload = Teacher(**provenance(), name="张三").model_dump(mode="json")

    parsed = TypeAdapter(CanonicalEntity).validate_python(payload)

    assert isinstance(parsed, Teacher)


def test_source_row_number_is_one_based() -> None:
    with pytest.raises(ValidationError):
        Teacher(**{**provenance(), "raw_row_number": 0}, name="张三")


def test_partial_scope_disables_redundant_detection() -> None:
    scope = SnapshotScope(
        tenant_id="school-1",
        scope_id="campus-a",
        mode=SnapshotMode.PARTIAL,
    )

    assert scope.allows_redundant_detection is False

from uuid import uuid4

from app.reconciliation.agent_identity import identity_postings, ordinary_field_differences
from app.schemas.agent_ingestion import AgentContractRecord, AgentEntityKind, AgentSourceRole


def _record(**changes: object) -> AgentContractRecord:
    values: dict[str, object] = {
        "task_id": uuid4(), "run_id": uuid4(), "snapshot_id": uuid4(), "tenant_id": "school-1",
        "source_role": AgentSourceRole.TARGET, "stable_locator": "csv:2", "stable_order": 1,
        "entity_kind": AgentEntityKind.STUDENT, "category": "学生", "name": "李四",
        "number": "S1", "class_name": "一班", "phone": "13800138000", "email": "s@example.test",
    }
    values.update(changes)
    return AgentContractRecord.model_validate(values)


def test_identity_postings_only_use_number_phone_and_email() -> None:
    record = _record()

    assert identity_postings(record) == (
        ("number", "S1"), ("phone", "13800138000"), ("email", "s@example.test"),
    )


def test_identity_fields_remain_governed_differences_after_correspondence() -> None:
    authority = _record(
        source_role=AgentSourceRole.AUTHORITATIVE,
        name="李四",
        number="S-001",
        class_name="二班",
        phone="13800138000",
        email="authority@example.test",
    )
    target = _record(
        name="李四同学",
        number="S-OLD",
        class_name="一班",
        phone="13800138001",
        email="target@example.test",
    )

    assert ordinary_field_differences(authority, target) == (
        "name",
        "number",
        "class_name",
        "phone",
        "email",
    )


def test_category_aliases_do_not_create_a_false_field_difference() -> None:
    authority = _record(
        source_role=AgentSourceRole.AUTHORITATIVE,
        entity_kind=AgentEntityKind.TEACHER,
        category="教师",
        class_name=None,
    )
    target = _record(
        entity_kind=AgentEntityKind.TEACHER,
        category="老师",
        class_name=None,
    )

    assert ordinary_field_differences(authority, target) == ()

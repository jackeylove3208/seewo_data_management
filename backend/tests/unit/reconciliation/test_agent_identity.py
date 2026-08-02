from types import SimpleNamespace
from uuid import uuid4

from app.reconciliation import agent_identity
from app.reconciliation.agent_identity import ordinary_field_differences
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


def test_provider_unavailable_fields_are_not_ordinary_differences() -> None:
    authority = _record(
        source_role=AgentSourceRole.AUTHORITATIVE,
        number=None,
        email=None,
    )
    target = _record(
        number="S-TARGET",
        email="target@example.test",
    )

    assert ordinary_field_differences(
        authority,
        target,
        unavailable_fields={"number", "email"},
    ) == ()


def test_unavailable_student_class_remains_a_governed_difference() -> None:
    authority = _record(
        source_role=AgentSourceRole.AUTHORITATIVE,
        class_name=None,
    )
    target = _record(class_name="一班")

    assert ordinary_field_differences(
        authority,
        target,
        unavailable_fields={"class_name"},
    ) == ("class_name",)


def test_frozen_identity_candidate_masks_phone_and_email_before_persistence() -> None:
    candidate = agent_identity._masked_candidate(  # noqa: SLF001
        SimpleNamespace(
            id=uuid4(),
            entity_kind="student",
            category="学生",
            name="测试学生",
            number="S-001",
            class_name="一班",
            phone="13812345678",
            email="secret.person@example.test",
        )
    )

    assert candidate["phone"] == "***5678"
    assert candidate["email"] == "s***@example.test"
    assert "secret.person" not in str(candidate)

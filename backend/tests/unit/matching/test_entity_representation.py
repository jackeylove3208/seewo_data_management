from uuid import uuid4

from app.ai.tokenization import TaskTokenizationContext
from app.matching.vector_index import representation
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import NormalizedRecord


def _student(source_id: str, *, name: str, phone: str, email: str) -> NormalizedRecord:
    return NormalizedRecord(
        entity_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        entity_type=EntityType.STUDENT,
        source_id=source_id,
        values={
            "display_name": name,
            "phone": phone,
            "email": email,
            "grade": "高一",
            "class_name": "高一1班",
            "status": "active",
            "unapproved_field": "must-not-leave-process",
        },
        rule_version="normalization-v1",
    )


def test_person_representation_tokenizes_protected_fields_and_allows_only_governed_fields() -> None:
    task_id = uuid4()
    context = TaskTokenizationContext(
        secret="test-secret-at-least-16-characters",
        tenant_id="school-1",
        task_id=task_id,
    )
    record = _student(
        "student-raw-id",
        name="张三",
        phone="13800000000",
        email="zhangsan@example.test",
    )

    text = representation(record, tokenization_context=context)

    for protected in ("student-raw-id", "张三", "13800000000", "zhangsan@example.test"):
        assert protected not in text
    assert "unapproved_field" not in text
    assert "PERSON_NAME_" in text
    assert "PHONE_" in text
    assert "EMAIL_" in text
    assert "EXTERNAL_ID_" in text
    assert "高一1班" in text

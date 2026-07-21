from uuid import uuid4

import pytest

from app.ai.tokenization import TaskTokenizationContext, UnknownTokenError


def context(*, task_id=None) -> TaskTokenizationContext:
    return TaskTokenizationContext(
        secret="enterprise-tokenization-secret",
        tenant_id="school-1",
        task_id=task_id or uuid4(),
    )


def test_tokens_are_stable_within_one_task_and_different_between_tasks() -> None:
    task_id = uuid4()
    first = context(task_id=task_id)
    second = context(task_id=task_id)
    other = context()

    first_token = first.tokenize_value("phone", "13100000000")

    assert first_token == second.tokenize_value("phone", "13100000000")
    assert first_token != other.tokenize_value("phone", "13100000000")
    assert first_token.startswith("PHONE_")


def test_recursive_tokenization_protects_person_values_but_keeps_organization_name() -> None:
    tokenizer = context()
    payload = {
        "source_payload": {
            "entity_type": "teacher",
            "name": "张三",
            "phone": "13100000000",
            "email": "zhangsan@example.com",
            "source_id": "T-001",
            "target_source_identifier": "SW-T-001",
            "department_source_id": "D-001",
        },
        "related_entities": [
            {
                "entity_type": "organization_unit",
                "name": "七年级",
                "source_id": "D-001",
            }
        ],
    }

    safe = tokenizer.tokenize(payload)

    assert safe["source_payload"]["name"].startswith("PERSON_NAME_")
    assert safe["source_payload"]["phone"].startswith("PHONE_")
    assert safe["source_payload"]["email"].startswith("EMAIL_")
    assert safe["source_payload"]["source_id"].startswith("EXTERNAL_ID_")
    assert safe["source_payload"]["target_source_identifier"].startswith("EXTERNAL_ID_")
    assert safe["source_payload"]["department_source_id"].startswith("EXTERNAL_ID_")
    assert safe["related_entities"][0]["name"] == "七年级"
    assert "张三" not in str(safe)
    assert "13100000000" not in str(safe)


def test_known_tokens_detokenize_without_persisted_mapping() -> None:
    tokenizer = context()
    safe = tokenizer.tokenize({"entity_type": "student", "name": "李四", "source_id": "S1"})

    restored = tokenizer.detokenize(safe)

    assert restored == {"entity_type": "student", "name": "李四", "source_id": "S1"}


def test_field_difference_values_use_the_declared_field_category() -> None:
    tokenizer = context()
    payload = {
        "entity_type": "teacher",
        "fields": [
            {
                "field": "phone",
                "source_value": "13800000000",
                "target_value": "13900000000",
                "normalized_source": "13800000000",
                "normalized_target": "13900000000",
            }
        ],
    }

    safe = tokenizer.tokenize(payload)
    field = safe["fields"][0]

    assert field["source_value"].startswith("PHONE_")
    assert field["target_value"].startswith("PHONE_")
    assert field["normalized_source"].startswith("PHONE_")
    assert field["normalized_target"].startswith("PHONE_")
    assert "13800000000" not in str(safe)


def test_unknown_token_is_rejected() -> None:
    tokenizer = context()

    with pytest.raises(UnknownTokenError, match="unknown model token"):
        tokenizer.detokenize({"after": "PHONE_DEADBEEF1234"})

from uuid import uuid4

import pytest

from app.ai.agent_phone_privacy import StudentPhoneTokenizationContext, UnknownStudentPhoneToken


def test_tokenizes_student_phone_with_task_scoped_opaque_token() -> None:
    tokenizer = StudentPhoneTokenizationContext(
        secret="s" * 16, tenant_id="school-1", task_id=uuid4()
    )

    token = tokenizer.tokenize("13800138000", entity_kind="student")

    assert token.startswith("STUDENT_PHONE_")
    assert "13800138000" not in token
    assert tokenizer.tokenize("13800138000", entity_kind="teacher") == "13800138000"


def test_rejects_unknown_model_phone_token() -> None:
    tokenizer = StudentPhoneTokenizationContext(
        secret="s" * 16, tenant_id="school-1", task_id=uuid4()
    )

    with pytest.raises(UnknownStudentPhoneToken):
        tokenizer.assert_known_tokens({"STUDENT_PHONE_000000000000"})

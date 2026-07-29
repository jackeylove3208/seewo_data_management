from types import SimpleNamespace

import pytest

from app.agent_runtime.csv_governance_handlers import (
    _changed_values,
    _require_target_identity,
)


def _record(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "entity_kind": "teacher",
        "category": "老师",
        "name": "测试教师",
        "number": "T-001",
        "class_name": None,
        "phone": "13800138000",
        "email": "teacher@example.test",
        "stable_locator": "csv:37",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_changed_values_use_semantic_fields_and_raw_csv_before_values() -> None:
    authority = _record(category="教师", phone="13900139000")
    target = _record(category="老师", phone="13800138000")

    before, after = _changed_values(
        target,
        authority,
        raw_target_values={
            "category": "教师",
            "phone": "+86 13800138000",
        },
    )

    assert before == {"phone": "+86 13800138000"}
    assert after == {"phone": "13900139000"}


def test_changed_values_allow_cross_category_target_without_student_fields() -> None:
    authority = _record(
        entity_kind="student",
        category="学生",
        number="S-001",
        class_name="一年级一班",
    )
    target = _record(
        entity_kind="teacher",
        category="老师",
        number="S-001",
        class_name=None,
    )

    before, after = _changed_values(
        target,
        authority,
        raw_target_values={"category": "老师", "number": "S-001"},
    )

    assert before == {"category": "老师", "class_name": None}
    assert after == {"category": "学生", "class_name": "一年级一班"}


def test_target_identity_guard_accepts_the_analyzed_entity() -> None:
    _require_target_identity(
        _record(),
        {
            "category": "老师",
            "name": "测试教师",
            "number": "T-001",
        },
    )


def test_target_identity_guard_rejects_a_locator_for_another_entity() -> None:
    with pytest.raises(ValueError, match="different entity.*name, number"):
        _require_target_identity(
            _record(),
            {
                "category": "老师",
                "name": "其他教师",
                "number": "T-999",
            },
        )

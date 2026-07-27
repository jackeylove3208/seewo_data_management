from types import SimpleNamespace

from app.agent_runtime.csv_governance_handlers import _changed_values


def _record(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "entity_kind": "teacher",
        "category": "老师",
        "name": "测试教师",
        "number": "T-001",
        "class_name": None,
        "phone": "13800138000",
        "email": "teacher@example.test",
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

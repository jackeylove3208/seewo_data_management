from app.differences.field_policies import FieldComparisonPolicy
from app.schemas.canonical_entities import EntityType


def test_phone_formatting_is_equivalent() -> None:
    policy = FieldComparisonPolicy()

    fields = policy.compare(
        EntityType.TEACHER,
        {"phone": "13800000000"},
        {"phone": "+86 138-0000-0000"},
    )

    assert fields == ()


def test_department_change_is_structural() -> None:
    fields = FieldComparisonPolicy().compare(
        EntityType.TEACHER,
        {"parent_mapping_id": "department-1"},
        {"parent_mapping_id": "department-2"},
    )

    assert len(fields) == 1
    assert fields[0].field == "parent_mapping_id"
    assert fields[0].comparison == "structure"


def test_ungoverned_raw_field_is_ignored() -> None:
    fields = FieldComparisonPolicy().compare(
        EntityType.STUDENT,
        {"import_note": "source-only"},
        {"import_note": "target-only"},
    )

    assert fields == ()


def test_null_representations_are_equivalent() -> None:
    fields = FieldComparisonPolicy().compare(
        EntityType.TEACHER,
        {"email": "  "},
        {"email": None},
    )

    assert fields == ()


def test_raw_and_normalized_values_are_both_preserved() -> None:
    fields = FieldComparisonPolicy().compare(
        EntityType.TEACHER,
        {"name": "张三", "phone": "13800000000"},
        {"name": "张三", "phone": "13900000000"},
        source_raw={"name": " 张三 ", "phone": "138 0000 0000"},
        target_raw={"name": "张三", "phone": "+86 139-0000-0000"},
    )

    assert len(fields) == 1
    assert fields[0].field == "phone"
    assert fields[0].source_value == "138 0000 0000"
    assert fields[0].target_value == "+86 139-0000-0000"
    assert fields[0].normalized_source == "13800000000"
    assert fields[0].normalized_target == "13900000000"


def test_default_policy_has_version_provenance() -> None:
    assert FieldComparisonPolicy().version == "comparison-v1"


def test_unresolved_structure_is_deferred() -> None:
    fields = FieldComparisonPolicy().compare(
        EntityType.TEACHER,
        {"parent_mapping_id": "__unresolved_relation__"},
        {"parent_mapping_id": "department-1"},
    )

    assert fields == ()

from uuid import uuid4

import pytest

from app.matching.exact_matcher import ExactMatcher
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import MatchMethod, MatchStatus, NormalizedRecord


def record(
    *,
    entity_type: EntityType = EntityType.TEACHER,
    source_id: str,
    values: dict[str, str | None],
    tenant_id: str = "school-1",
    parent_mapping_id=None,
) -> NormalizedRecord:
    return NormalizedRecord(
        entity_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id=tenant_id,
        entity_type=entity_type,
        source_id=source_id,
        values=values,
        parent_mapping_id=parent_mapping_id,
        rule_version="normalization-v1",
    )


def test_teacher_employee_number_beats_name() -> None:
    source = record(
        source_id="third-7",
        values={"display_name": "张三", "employee_number": "E007"},
    )
    targets = [
        record(
            source_id="seewo-a",
            values={"display_name": "李四", "employee_number": "E007"},
        ),
        record(
            source_id="seewo-b",
            values={"display_name": "张三", "employee_number": "E008"},
        ),
    ]

    decision = ExactMatcher().match(source, targets)

    assert decision is not None
    assert decision.method is MatchMethod.STABLE_ID
    assert decision.target_entity_id == targets[0].entity_id
    assert decision.evidence[0].feature == "employee_number"


def test_duplicate_stable_id_routes_to_conflict() -> None:
    source = record(source_id="third-7", values={"employee_number": "E007"})
    targets = [
        record(source_id="seewo-a", values={"employee_number": "E007"}),
        record(source_id="seewo-b", values={"employee_number": "E007"}),
    ]

    decision = ExactMatcher().match(source, targets)

    assert decision is not None
    assert decision.status is MatchStatus.CONFLICT
    assert decision.target_entity_id is None


def test_name_alone_is_not_exact() -> None:
    source = record(source_id="third-7", values={"display_name": "张三"})
    target = record(source_id="seewo-a", values={"display_name": "张三"})

    assert ExactMatcher().match(source, [target]) is None


def test_class_composite_key_requires_mapped_parent() -> None:
    parent_mapping_id = uuid4()
    source = record(
        entity_type=EntityType.CLASS,
        source_id="third-c1",
        parent_mapping_id=parent_mapping_id,
        values={"school_year": "2024", "grade": "高一", "class_number": "1"},
    )
    target = record(
        entity_type=EntityType.CLASS,
        source_id="seewo-c9",
        parent_mapping_id=parent_mapping_id,
        values={"school_year": "2024", "grade": "高一", "class_number": "1"},
    )

    decision = ExactMatcher().match(source, [target])

    assert decision is not None
    assert decision.method is MatchMethod.COMPOSITE_KEY


def test_malformed_contact_values_are_not_stable_keys() -> None:
    source = record(
        source_id="third-7",
        values={"phone": "123", "email": "foo"},
    )
    target = record(
        source_id="seewo-a",
        values={"phone": "123", "email": "foo"},
    )

    assert ExactMatcher().match(source, [target]) is None


def test_custom_exact_key_policy_requires_distinct_provenance() -> None:
    with pytest.raises(ValueError, match="rule_version"):
        ExactMatcher({EntityType.TEACHER: (("source_id",),)})


def test_custom_exact_policy_for_one_type_keeps_other_default_policies() -> None:
    matcher = ExactMatcher(
        {EntityType.TEACHER: (("employee_number",),)},
        rule_version="exact-teacher-v2",
    )
    source = record(
        entity_type=EntityType.ORGANIZATION_UNIT,
        source_id="source-dept",
        values={"code": "DEPT-1"},
    )
    target = record(
        entity_type=EntityType.ORGANIZATION_UNIT,
        source_id="target-dept",
        values={"code": "DEPT-1"},
    )

    decision = matcher.match(source, [target])

    assert decision is not None
    assert decision.status is MatchStatus.ACCEPTED

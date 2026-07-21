from uuid import uuid4

import pytest

from app.matching.exact_matcher import ExactMatcher
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import MatchMethod, MatchStatus, NormalizedRecord
from app.schemas.rematching import (
    KeyGroupPolicy,
    TrustedSourceIdentifierPolicy,
    VersionedKeyPolicy,
)
from tests.fixtures.matching_cases import obvious_student_cascade_case

_DEFAULT_SNAPSHOT_ID = uuid4()


def record(
    *,
    entity_type: EntityType = EntityType.TEACHER,
    source_id: str,
    values: dict[str, str | None],
    tenant_id: str = "school-1",
    parent_mapping_id=None,
    snapshot_id=None,
) -> NormalizedRecord:
    return NormalizedRecord(
        entity_id=uuid4(),
        snapshot_id=snapshot_id or _DEFAULT_SNAPSHOT_ID,
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


def test_legacy_source_id_policy_cannot_bypass_trust_with_rule_version() -> None:
    with pytest.raises(ValueError, match="source_id.*TrustedSourceIdentifierPolicy"):
        ExactMatcher(
            {EntityType.STUDENT: (("source_id",),)},
            rule_version="unsafe-source-id-v2",
        )


def test_versioned_source_id_group_cannot_bypass_trust_policy() -> None:
    unsafe = VersionedKeyPolicy(
        version="unsafe-source-id-v3",
        entity_type=EntityType.STUDENT,
        groups=(KeyGroupPolicy(name="platform-id", fields=("source_id",)),),
    )

    with pytest.raises(ValueError, match="source_id.*TrustedSourceIdentifierPolicy"):
        ExactMatcher(versioned_policies=(unsafe,))


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


def test_student_matches_when_one_complete_alternative_group_is_unique() -> None:
    source = record(
        entity_type=EntityType.STUDENT,
        source_id="third-student",
        values={
            "name": "李雷",
            "student_number": None,
            "phone": "13800000001",
            "email": None,
        },
    )
    target = record(
        entity_type=EntityType.STUDENT,
        source_id="seewo-student",
        values={
            "name": "李雷",
            "student_number": None,
            "phone": "13800000001",
            "email": None,
        },
    )

    decision = ExactMatcher().match(source, [target])

    assert decision is not None
    assert decision.status is MatchStatus.ACCEPTED
    assert decision.method is MatchMethod.COMPOSITE_KEY
    assert decision.rule_version == "student-keys-v2"
    assert [item.feature for item in decision.evidence] == [
        "name",
        "phone",
        "key_group:name_phone",
    ]


def test_partial_groups_do_not_claim_an_exact_match() -> None:
    policy = VersionedKeyPolicy(
        version="student-contact-v3",
        entity_type=EntityType.STUDENT,
        groups=(KeyGroupPolicy(name="name_phone", fields=("name", "phone")),),
    )
    matcher = ExactMatcher(versioned_policies=(policy,))
    source = record(
        entity_type=EntityType.STUDENT,
        source_id="third-student",
        values={"name": "李雷", "phone": None},
    )
    target = record(
        entity_type=EntityType.STUDENT,
        source_id="seewo-student",
        values={"name": "李雷", "phone": None},
    )

    assert matcher.match(source, [target]) is None


def test_complete_groups_pointing_to_different_targets_record_conflict_paths() -> None:
    source = record(
        entity_type=EntityType.STUDENT,
        source_id="third-student",
        values={"name": "李雷", "phone": "13800000001", "email": "lilei@example.com"},
    )
    phone_target = record(
        entity_type=EntityType.STUDENT,
        source_id="seewo-phone",
        values={"name": "李雷", "phone": "13800000001", "email": "other@example.com"},
    )
    email_target = record(
        entity_type=EntityType.STUDENT,
        source_id="seewo-email",
        values={"name": "李雷", "phone": "13900000001", "email": "lilei@example.com"},
    )

    decision = ExactMatcher().match(source, [phone_target, email_target])

    assert decision is not None
    assert decision.status is MatchStatus.CONFLICT
    assert {item.feature for item in decision.evidence} >= {
        "conflicting_key_group:name_phone",
        "conflicting_key_group:name_email",
    }


def test_non_unique_complete_group_blocks_other_unique_group() -> None:
    source = record(
        entity_type=EntityType.STUDENT,
        source_id="third-student",
        values={
            "name": "李雷",
            "student_number": "S-1",
            "phone": "13800000001",
        },
    )
    targets = [
        record(
            entity_type=EntityType.STUDENT,
            source_id="seewo-a",
            values={"name": "李雷", "student_number": "S-1", "phone": "13800000001"},
        ),
        record(
            entity_type=EntityType.STUDENT,
            source_id="seewo-b",
            values={"name": "李雷", "student_number": "S-2", "phone": "13800000001"},
        ),
    ]

    decision = ExactMatcher().match(source, targets)

    assert decision is not None
    assert decision.status is MatchStatus.CONFLICT
    assert any(item.feature == "non_unique_key_group:name_phone" for item in decision.evidence)


def test_mixed_target_snapshots_require_and_apply_explicit_snapshot_filter() -> None:
    source = record(
        entity_type=EntityType.STUDENT,
        source_id="third-student",
        values={"student_number": "S-1"},
    )
    first_snapshot_id = uuid4()
    second_snapshot_id = uuid4()
    stale = record(
        entity_type=EntityType.STUDENT,
        source_id="stale-target",
        values={"student_number": "S-1"},
        snapshot_id=first_snapshot_id,
    )
    current = record(
        entity_type=EntityType.STUDENT,
        source_id="current-target",
        values={"student_number": "S-1"},
        snapshot_id=second_snapshot_id,
    )
    matcher = ExactMatcher()
    index = matcher.build_index((stale, current))

    with pytest.raises(ValueError, match="target_snapshot_id"):
        matcher.match(source, index)

    decision = matcher.match(source, index, target_snapshot_id=second_snapshot_id)

    assert decision is not None
    assert decision.status is MatchStatus.ACCEPTED
    assert decision.target_entity_id == current.entity_id


def test_source_id_requires_trust_for_exact_matching_and_is_pair_scoped() -> None:
    source = record(
        entity_type=EntityType.STUDENT,
        source_id="SHARED-1",
        values={},
        tenant_id="school-1",
    )
    target = NormalizedRecord(
        entity_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        entity_type=EntityType.STUDENT,
        source_id="SHARED-1",
        values={},
        rule_version="normalization-v1",
    )
    untrusted = TrustedSourceIdentifierPolicy(
        version="source-id-v1",
        tenant_id="school-1",
        entity_type=EntityType.STUDENT,
        source_snapshot_id=source.snapshot_id,
        target_snapshot_id=target.snapshot_id,
    )
    trusted_wrong_pair = untrusted.model_copy(
        update={"version": "source-id-v2", "trusted": True, "target_snapshot_id": uuid4()}
    )
    trusted = untrusted.model_copy(update={"version": "source-id-v2", "trusted": True})
    wrong_tenant_target = target.model_copy(update={"tenant_id": "school-2"})

    assert ExactMatcher().match(source, [target]) is None
    assert ExactMatcher(source_id_trust_policies=(untrusted,)).match(source, [target]) is None
    assert (
        ExactMatcher(source_id_trust_policies=(trusted_wrong_pair,)).match(source, [target]) is None
    )
    assert (
        ExactMatcher(source_id_trust_policies=(trusted,)).match(source, [wrong_tenant_target])
        is None
    )
    decision = ExactMatcher(source_id_trust_policies=(trusted,)).match(source, [target])
    assert decision is not None
    assert decision.status is MatchStatus.ACCEPTED
    assert decision.rule_version == "source-id-v2"


def test_473_obvious_students_survive_unresolved_class_cascade() -> None:
    case = obvious_student_cascade_case()
    baseline = ExactMatcher(
        {EntityType.STUDENT: (("student_number",),)},
        rule_version="student-number-only-v1",
    )
    matcher = ExactMatcher()
    index = matcher.build_index(case.targets)

    baseline_decisions = [baseline.match(source, case.targets) for source in case.sources]
    decisions = [matcher.match(source, index) for source in case.sources]

    assert case.source_class_name != case.target_class_name
    assert len(decisions) == 473
    assert all(source.values["student_number"] is None for source in case.sources)
    assert all(source.parent_mapping_id is None for source in case.sources)
    assert all(target.parent_mapping_id is not None for target in case.targets)
    assert all(decision is None for decision in baseline_decisions)
    assert all(
        decision is not None and decision.status is MatchStatus.ACCEPTED for decision in decisions
    )

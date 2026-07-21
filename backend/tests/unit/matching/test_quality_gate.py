from uuid import uuid4

from app.matching.quality import MatchingQualityPolicy
from app.schemas.canonical_entities import EntityType
from app.schemas.rematching import MatchingQualityCounts


def counts(
    *,
    total: int,
    accepted: int,
    manual_review: int = 0,
    conflict: int = 0,
    unmatched: int = 0,
    unconsumed_target: int = 0,
    ai_recovered: int = 0,
) -> MatchingQualityCounts:
    return MatchingQualityCounts(
        total=total,
        accepted=accepted,
        deterministic=accepted - ai_recovered,
        ai_recovered=ai_recovered,
        manual_review=manual_review,
        conflict=conflict,
        unmatched=unmatched,
        unconsumed_target=unconsumed_target,
        predicted_missing=unmatched,
        predicted_redundant=unconsumed_target,
    )


def test_quality_policy_blocks_large_unresolved_student_ratio() -> None:
    result = MatchingQualityPolicy().evaluate(
        task_id=uuid4(),
        mapping_versions=("mapping-v1",),
        counts_by_type={
            EntityType.CLASS: counts(total=15, accepted=15),
            EntityType.STUDENT: counts(total=100, accepted=79, unmatched=21),
        },
    )

    assert result.passed is False
    assert any(
        "学生" in failure.reason and failure.observed_value == 0.21 for failure in result.failures
    )


def test_quality_policy_ignores_ratio_gate_below_minimum_population() -> None:
    result = MatchingQualityPolicy().evaluate(
        task_id=uuid4(),
        mapping_versions=("mapping-v1",),
        counts_by_type={EntityType.STUDENT: counts(total=9, accepted=0, unmatched=9)},
    )

    assert result.passed is True
    assert result.failures == ()


def test_quality_policy_blocks_children_when_no_parent_is_accepted() -> None:
    result = MatchingQualityPolicy().evaluate(
        task_id=uuid4(),
        mapping_versions=("mapping-v1",),
        counts_by_type={
            EntityType.CLASS: counts(total=15, accepted=0, manual_review=15),
            EntityType.STUDENT: counts(total=100, accepted=100),
        },
    )

    assert result.passed is False
    assert any(
        failure.affected_entity_types == (EntityType.STUDENT,) and "班级" in failure.reason
        for failure in result.failures
    )


def test_quality_policy_blocks_abnormal_predicted_disable_volume() -> None:
    result = MatchingQualityPolicy().evaluate(
        task_id=uuid4(),
        mapping_versions=("mapping-v1",),
        counts_by_type={
            EntityType.STUDENT: counts(
                total=100,
                accepted=100,
                unconsumed_target=26,
            )
        },
    )

    assert result.passed is False
    assert any("停用" in failure.reason for failure in result.failures)


def test_quality_policy_passes_bounded_reconciliation_output() -> None:
    result = MatchingQualityPolicy().evaluate(
        task_id=uuid4(),
        mapping_versions=("mapping-v3",),
        counts_by_type={
            EntityType.CLASS: counts(total=15, accepted=15),
            EntityType.STUDENT: counts(
                total=100,
                accepted=97,
                unmatched=3,
                unconsumed_target=4,
                ai_recovered=20,
            ),
        },
    )

    assert result.passed is True
    assert result.policy_version == "matching-quality-v1"

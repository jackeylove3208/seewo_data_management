from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.canonical_entities import EntityType
from app.schemas.rematching import (
    KeyGroupPolicy,
    MatchingQualityCounts,
    MatchingQualityGate,
    MatchingQualityResult,
    TrustedSourceIdentifierPolicy,
    VersionedKeyPolicy,
)


def test_versioned_policy_models_or_of_complete_and_groups() -> None:
    policy = VersionedKeyPolicy(
        version="student-keys-v2",
        entity_type=EntityType.STUDENT,
        groups=(
            KeyGroupPolicy(name="student_number", fields=("student_number",)),
            KeyGroupPolicy(name="name_phone", fields=("name", "phone")),
        ),
    )

    assert policy.groups[1].is_complete({"name": "李雷", "phone": "13800000000"})
    assert not policy.groups[1].is_complete({"name": "李雷", "phone": " "})
    values = {"student_number": None, "name": "李雷", "phone": "13800000000"}
    assert policy.complete_groups(values) == (policy.groups[1],)


def test_key_policy_rejects_duplicate_group_keys_and_fields() -> None:
    with pytest.raises(ValidationError, match="group keys"):
        VersionedKeyPolicy(
            version="v1",
            entity_type=EntityType.STUDENT,
            groups=(
                KeyGroupPolicy(name="contact", fields=("name", "phone")),
                KeyGroupPolicy(name="contact", fields=("name", "email")),
            ),
        )
    with pytest.raises(ValidationError, match="fields"):
        KeyGroupPolicy(name="contact", fields=("name", "name"))


def test_source_identifier_is_untrusted_unless_explicitly_enabled_for_pair() -> None:
    source_snapshot_id, target_snapshot_id = uuid4(), uuid4()
    default_policy = TrustedSourceIdentifierPolicy(
        version="source-id-v1",
        tenant_id="school-1",
        entity_type=EntityType.STUDENT,
        source_snapshot_id=source_snapshot_id,
        target_snapshot_id=target_snapshot_id,
    )
    trusted_policy = TrustedSourceIdentifierPolicy(
        version="source-id-v2",
        tenant_id="school-1",
        entity_type=EntityType.STUDENT,
        source_snapshot_id=source_snapshot_id,
        target_snapshot_id=target_snapshot_id,
        trusted=True,
        field="student_number",
    )

    assert default_policy.trusted is False
    assert default_policy.can_auto_match is False
    assert trusted_policy.can_auto_match is True


def test_matching_quality_counts_validate_partition_and_ratios() -> None:
    counts = MatchingQualityCounts(
        total=10,
        accepted=7,
        deterministic=4,
        ai_recovered=3,
        manual_review=1,
        conflict=1,
        unmatched=1,
        unconsumed_target=2,
        predicted_missing=1,
        predicted_redundant=2,
    )
    assert counts.remaining_unresolved == 3
    assert counts.unresolved_ratio == pytest.approx(0.3)

    with pytest.raises(ValidationError, match="accepted count"):
        MatchingQualityCounts(
            total=10,
            accepted=6,
            deterministic=4,
            ai_recovered=3,
            manual_review=1,
            conflict=1,
            unmatched=2,
            unconsumed_target=0,
            predicted_missing=2,
            predicted_redundant=0,
        )


def test_failed_quality_gate_requires_actionable_chinese_failure() -> None:
    counts = MatchingQualityCounts(
        total=10,
        accepted=7,
        deterministic=4,
        ai_recovered=3,
        manual_review=1,
        conflict=1,
        unmatched=1,
        unconsumed_target=2,
        predicted_missing=1,
        predicted_redundant=2,
    )
    failed_gate = MatchingQualityGate(
        code="matching_quality_gate_failed",
        affected_entity_types=(EntityType.STUDENT,),
        reason="学生未解析比例超过安全阈值",
        observed_value=0.3,
        threshold=0.2,
        recovery_actions=("确认班级映射", "重试实体匹配"),
    )
    result = MatchingQualityResult(
        task_id=uuid4(),
        policy_version="quality-v1",
        mapping_versions=("mapping-v3",),
        counts={EntityType.STUDENT: counts},
        passed=False,
        failures=(failed_gate,),
    )
    assert result.retryable is True

    with pytest.raises(ValidationError, match="failed result"):
        MatchingQualityResult(
            task_id=uuid4(),
            policy_version="quality-v1",
            mapping_versions=("mapping-v3",),
            counts={EntityType.STUDENT: counts},
            passed=False,
        )

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.canonical_entities import EntityType
from app.schemas.rematching import (
    AcceptCandidateDecision,
    CandidateEdge,
    CandidateRole,
    KeyFieldEvidence,
    KeyGroupEvidence,
    KeyGroupPolicy,
    KeyPolicyEvidence,
    ManualReviewDecision,
    MatchingQualityCounts,
    MatchingQualityGate,
    MatchingQualityResult,
    NoMatchDecision,
    RematchDecision,
    RematchDecisionRequest,
    RematchingJobProgress,
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


def test_key_group_evidence_distinguishes_complete_and_partial_groups() -> None:
    target_id = uuid4()
    evidence = KeyGroupEvidence(
        policy_version="student-keys-v2",
        group_key="name_phone",
        required_fields=("name", "phone"),
        fields=(
            KeyFieldEvidence(field="name", source_value="李雷", target_value="李雷", matched=True),
            KeyFieldEvidence(
                field="phone",
                source_value="13800000000",
                target_value="13800000000",
                matched=True,
            ),
        ),
        candidate_entity_ids=(target_id,),
    )

    assert evidence.complete is True
    assert evidence.unique_target_id == target_id

    partial = KeyGroupEvidence(
        policy_version="student-keys-v2",
        group_key="name_phone",
        required_fields=("name", "phone"),
        fields=(
            KeyFieldEvidence(field="name", source_value="李雷", target_value="李雷", matched=True),
        ),
        candidate_entity_ids=(),
    )
    assert partial.complete is False
    assert partial.unique_target_id is None


@pytest.mark.parametrize(
    ("target_value", "matched"),
    ((None, True), ("李雷", False)),
)
def test_key_group_is_incomplete_without_valid_matching_target_evidence(
    target_value: str | None, matched: bool
) -> None:
    evidence = KeyGroupEvidence(
        policy_version="student-keys-v2",
        group_key="name",
        required_fields=("name",),
        fields=(
            KeyFieldEvidence(
                field="name",
                source_value="李雷",
                target_value=target_value,
                matched=matched,
            ),
        ),
        candidate_entity_ids=(uuid4(),),
    )

    assert evidence.complete is False
    assert evidence.unique_target_id is None


def test_policy_evidence_only_resolves_when_complete_groups_agree_uniquely() -> None:
    target_a, target_b = uuid4(), uuid4()

    def group(name: str, candidates: tuple) -> KeyGroupEvidence:
        return KeyGroupEvidence(
            policy_version="student-keys-v2",
            group_key=name,
            required_fields=("name",),
            fields=(
                KeyFieldEvidence(
                    field="name", source_value="李雷", target_value="李雷", matched=True
                ),
            ),
            candidate_entity_ids=candidates,
        )

    unique = KeyPolicyEvidence(
        policy_version="student-keys-v2",
        groups=(group("name_phone", (target_a,)), group("name_email", (target_a,))),
    )
    conflicting = KeyPolicyEvidence(
        policy_version="student-keys-v2",
        groups=(group("name_phone", (target_a,)), group("name_email", (target_b,))),
    )
    non_unique = KeyPolicyEvidence(
        policy_version="student-keys-v2",
        groups=(group("shared_phone", (target_a, target_b)),),
    )

    assert unique.unique_target_id == target_a
    assert conflicting.unique_target_id is None
    assert conflicting.conflicting_target_ids == frozenset({target_a, target_b})
    assert non_unique.unique_target_id is None


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


def test_candidate_edge_preserves_server_candidate_identity_and_direction() -> None:
    focal_id, candidate_id = uuid4(), uuid4()
    edge = CandidateEdge(
        focal_entity_id=focal_id,
        focal_role=CandidateRole.AUTHORITATIVE,
        candidate_entity_id=candidate_id,
        candidate_role=CandidateRole.TARGET,
        rank=1,
        vector_score=0.98,
        representation_version="student-v1",
    )

    assert edge.candidate_entity_id == candidate_id
    with pytest.raises(ValidationError, match="opposite source roles"):
        CandidateEdge(
            focal_entity_id=focal_id,
            focal_role=CandidateRole.TARGET,
            candidate_entity_id=candidate_id,
            candidate_role=CandidateRole.TARGET,
            rank=1,
            representation_version="student-v1",
        )


def test_rematch_decision_is_a_discriminated_union() -> None:
    adapter = TypeAdapter(RematchDecision)
    candidate_id = uuid4()

    accepted = adapter.validate_python(
        {
            "decision": "accept_candidate",
            "candidate_entity_id": str(candidate_id),
            "confidence": 0.97,
            "reason": "姓名和手机号一致",
            "strong_evidence_features": ["name", "phone"],
        }
    )
    no_match = adapter.validate_python(
        {"decision": "no_match", "confidence": 0.92, "reason": "候选人的手机号均不一致"}
    )
    manual = adapter.validate_python(
        {"decision": "manual_review", "confidence": 0.4, "reason": "候选证据冲突，需要人工确认"}
    )

    assert isinstance(accepted, AcceptCandidateDecision)
    assert isinstance(no_match, NoMatchDecision)
    assert isinstance(manual, ManualReviewDecision)


def test_decisions_require_chinese_business_reasons() -> None:
    with pytest.raises(ValidationError, match="Chinese"):
        ManualReviewDecision(confidence=0.1, reason="Gateway unavailable")


def test_request_rejects_model_candidate_id_outside_server_candidates() -> None:
    allowed_id = uuid4()
    with pytest.raises(ValidationError, match="server-owned candidate set"):
        RematchDecisionRequest(
            focal_entity_id=uuid4(),
            server_candidate_ids=(allowed_id,),
            decision=AcceptCandidateDecision(
                candidate_entity_id=uuid4(),
                confidence=0.99,
                reason="姓名和手机号一致",
                strong_evidence_features=("name", "phone"),
            ),
        )


def test_accept_candidate_requires_two_distinct_strong_features() -> None:
    with pytest.raises(ValidationError, match="two distinct"):
        AcceptCandidateDecision(
            candidate_entity_id=uuid4(),
            confidence=0.99,
            reason="姓名相似",
            strong_evidence_features=("name", "name"),
        )


def test_rematching_progress_counters_are_consistent() -> None:
    progress = RematchingJobProgress(
        initial_unresolved=10,
        indexed=10,
        processed=8,
        ai_recovered=3,
        no_match=2,
        manual_review=1,
        conflict=1,
        failed=1,
    )
    assert progress.remaining == 2

    with pytest.raises(ValidationError, match="processed count"):
        RematchingJobProgress(
            initial_unresolved=10,
            indexed=10,
            processed=7,
            ai_recovered=3,
            no_match=2,
            manual_review=1,
            conflict=1,
            failed=1,
        )


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

from uuid import uuid4

import pytest

from app.ai.rematching_policy import (
    RematchingPolicyError,
    manual_review_fallback,
    validate_rematch_decision,
)
from app.schemas.rematching import (
    AcceptCandidateDecision,
    CandidateEdge,
    CandidateRole,
    KeyFieldEvidence,
    ManualReviewDecision,
    RematchDecisionRequest,
)


def accepted_request(*, confidence: float = 0.96) -> tuple[RematchDecisionRequest, CandidateEdge]:
    focal_id, candidate_id = uuid4(), uuid4()
    edge = CandidateEdge(
        focal_entity_id=focal_id,
        focal_role=CandidateRole.AUTHORITATIVE,
        candidate_entity_id=candidate_id,
        candidate_role=CandidateRole.TARGET,
        rank=1,
        vector_score=0.95,
        lexical_score=1,
        representation_version="student-v1",
        evidence=(
            KeyFieldEvidence(
                field="name",
                source_value="PERSON_NAME_123",
                target_value="PERSON_NAME_123",
                matched=True,
            ),
            KeyFieldEvidence(
                field="phone",
                source_value="PHONE_123",
                target_value="PHONE_123",
                matched=True,
            ),
        ),
    )
    request = RematchDecisionRequest(
        focal_entity_id=focal_id,
        server_candidate_ids=(candidate_id,),
        decision=AcceptCandidateDecision(
            candidate_entity_id=candidate_id,
            confidence=confidence,
            reason="姓名和手机号均与候选记录一致。",
            strong_evidence_features=("name", "phone"),
        ),
    )
    return request, edge


def test_accepts_high_confidence_server_candidate_with_two_real_features() -> None:
    request, edge = accepted_request()

    assert validate_rematch_decision(request, candidate_edges=(edge,)) is request.decision


def test_rejects_acceptance_below_high_confidence_threshold() -> None:
    request, edge = accepted_request(confidence=0.89)

    with pytest.raises(RematchingPolicyError, match="confidence"):
        validate_rematch_decision(request, candidate_edges=(edge,))


def test_rejects_feature_claim_not_supported_by_candidate_edge() -> None:
    request, edge = accepted_request()
    unsupported = request.model_copy(
        update={
            "decision": request.decision.model_copy(
                update={"strong_evidence_features": ("name", "student_number")}
            )
        }
    )

    with pytest.raises(RematchingPolicyError, match="strong evidence"):
        validate_rematch_decision(unsupported, candidate_edges=(edge,))


def test_rejects_edge_for_a_different_focal_entity() -> None:
    request, edge = accepted_request()
    wrong_edge = edge.model_copy(update={"focal_entity_id": uuid4()})

    with pytest.raises(RematchingPolicyError, match="candidate edge"):
        validate_rematch_decision(request, candidate_edges=(wrong_edge,))


def test_non_executable_decisions_do_not_require_candidate_evidence() -> None:
    request, _edge = accepted_request()
    manual = request.model_copy(
        update={
            "decision": ManualReviewDecision(
                confidence=0.3,
                reason="候选记录证据冲突，需要人工确认。",
            )
        }
    )

    assert validate_rematch_decision(manual, candidate_edges=()) is manual.decision


def test_safe_fallback_is_actionable_chinese_manual_review() -> None:
    fallback = manual_review_fallback()

    assert isinstance(fallback, ManualReviewDecision)
    assert fallback.decision == "manual_review"
    assert "人工" in fallback.reason

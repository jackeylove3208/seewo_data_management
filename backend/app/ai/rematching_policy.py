from collections.abc import Sequence

from app.schemas.rematching import (
    AcceptCandidateDecision,
    CandidateEdge,
    ManualReviewDecision,
    RematchDecision,
    RematchDecisionRequest,
)


class RematchingPolicyError(ValueError):
    pass


def validate_rematch_decision(
    request: RematchDecisionRequest,
    *,
    candidate_edges: Sequence[CandidateEdge],
    high_confidence_threshold: float = 0.9,
    top_k: int = 3,
) -> RematchDecision:
    decision = request.decision
    if not isinstance(decision, AcceptCandidateDecision):
        return decision
    if decision.confidence < high_confidence_threshold:
        raise RematchingPolicyError("candidate confidence is below the automatic threshold")
    edge = next(
        (
            item
            for item in candidate_edges
            if item.focal_entity_id == request.focal_entity_id
            and item.candidate_entity_id == decision.candidate_entity_id
            and item.rank <= top_k
        ),
        None,
    )
    if edge is None:
        raise RematchingPolicyError("accepted candidate edge is not server-owned")
    supported_features = {
        evidence.field
        for evidence in edge.evidence
        if evidence.matched
        and bool(evidence.source_value and evidence.source_value.strip())
        and bool(evidence.target_value and evidence.target_value.strip())
    }
    if not set(decision.strong_evidence_features).issubset(supported_features):
        raise RematchingPolicyError(
            "claimed strong evidence is not supported by the candidate edge"
        )
    return decision


def manual_review_fallback() -> ManualReviewDecision:
    return ManualReviewDecision(
        confidence=0,
        reason="自动二次匹配未形成安全结论，请人工检查候选记录。",
    )

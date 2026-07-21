from collections.abc import Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.schemas.canonical_entities import EntityType
from app.schemas.matching import (
    Candidate,
    MatchDecision,
    MatchEvidence,
    MatchMethod,
    MatchStatus,
    NormalizedRecord,
)

SCORE_POLICY_V1: dict[EntityType, dict[str, float]] = {
    EntityType.TEACHER: {
        "name": 0.35,
        "employee_number": 0.35,
        "phone": 0.15,
        "parent": 0.15,
    },
    EntityType.STUDENT: {"name": 0.30, "student_number": 0.50, "class": 0.20},
    EntityType.CLASS: {
        "name": 0.35,
        "grade": 0.25,
        "school_year": 0.20,
        "parent": 0.20,
    },
    EntityType.ORGANIZATION_UNIT: {"name": 0.55, "path": 0.25, "parent": 0.20},
    EntityType.MEMBERSHIP: {"member": 0.45, "container": 0.45, "role": 0.10},
}


@dataclass(frozen=True)
class _Scored:
    candidate: Candidate
    score: float
    evidence: tuple[MatchEvidence, ...]


class CandidateScorer:
    def __init__(
        self,
        *,
        threshold: float = 0.86,
        margin: float = 0.08,
        policy: dict[EntityType, dict[str, float]] | None = None,
        rule_version: str | None = None,
    ) -> None:
        if not 0 <= threshold <= 1 or not 0 <= margin <= 1:
            raise ValueError("threshold and margin must be between 0 and 1")
        customized = threshold != 0.86 or margin != 0.08 or policy is not None
        if customized and rule_version is None:
            raise ValueError("custom scoring rules require an explicit rule_version")
        self.threshold = threshold
        self.margin = margin
        self.policy = _validated_policy(policy)
        self.rule_version = rule_version or "scoring-v1"

    def decide(
        self,
        source: NormalizedRecord,
        candidates: Sequence[Candidate],
    ) -> MatchDecision:
        if not candidates:
            return MatchDecision(
                entity_type=source.entity_type,
                source_entity_id=source.entity_id,
                source_key=source.record_key,
                status=MatchStatus.UNMATCHED,
                confidence=0,
                rule_version=self.rule_version,
            )
        scored = sorted(
            (self._score(source, candidate) for candidate in _merge_candidates(candidates)),
            key=lambda item: (-item.score, str(item.candidate.entity_id)),
        )
        first = scored[0]
        second_score = scored[1].score if len(scored) > 1 else None
        status = decision_status(
            first.score,
            second_score,
            threshold=self.threshold,
            margin=self.margin,
        )
        decision_evidence = (
            *first.evidence,
            MatchEvidence(
                feature="retrieval_scope",
                source_value=(
                    str(source.parent_mapping_id) if source.parent_mapping_id is not None else None
                ),
                target_value=first.candidate.retrieval_scope,
                score=float(first.candidate.retrieval_scope == "strict"),
            ),
            MatchEvidence(
                feature="acceptance_threshold",
                source_value=str(self.threshold),
                target_value=None,
                score=self.threshold,
            ),
            MatchEvidence(
                feature="runner_up_score",
                source_value=None,
                target_value=scored[1].candidate.entity.record_key if len(scored) > 1 else None,
                score=second_score or 0,
            ),
            MatchEvidence(
                feature="score_margin",
                source_value=str(first.score - second_score) if second_score is not None else None,
                target_value=None,
                score=round(first.score - second_score, 6) if second_score is not None else 1,
            ),
            MatchEvidence(
                feature="required_margin",
                source_value=str(self.margin),
                target_value=None,
                score=self.margin,
            ),
        )
        return MatchDecision(
            entity_type=source.entity_type,
            source_entity_id=source.entity_id,
            source_key=source.record_key,
            target_entity_id=first.candidate.entity_id,
            target_key=first.candidate.entity.record_key,
            method=MatchMethod.SCORED,
            status=status,
            confidence=first.score,
            evidence=decision_evidence,
            rule_version=self.rule_version,
        )

    def _score(self, source: NormalizedRecord, candidate: Candidate) -> _Scored:
        target = candidate.entity
        weights = self.policy[source.entity_type]
        evidence = tuple(_feature_evidence(feature, source, target) for feature in weights)
        total = round(
            sum(weights[item.feature] * item.score for item in evidence),
            6,
        )
        return _Scored(candidate=candidate, score=total, evidence=evidence)


def decision_status(
    first: float,
    second: float | None,
    *,
    threshold: float = 0.86,
    margin: float = 0.08,
) -> MatchStatus:
    if first < threshold:
        return MatchStatus.MANUAL_REVIEW
    if second is not None and first - second < margin:
        return MatchStatus.MANUAL_REVIEW
    return MatchStatus.ACCEPTED


def _validated_policy(
    overrides: dict[EntityType, dict[str, float]] | None,
) -> dict[EntityType, dict[str, float]]:
    policy = {entity_type: dict(weights) for entity_type, weights in SCORE_POLICY_V1.items()}
    if overrides is None:
        return policy
    for entity_type, weights in overrides.items():
        expected_features = set(SCORE_POLICY_V1[entity_type])
        if set(weights) != expected_features:
            raise ValueError(
                f"custom {entity_type.value} scoring policy must define features: "
                f"{sorted(expected_features)}"
            )
        if any(weight < 0 or weight > 1 for weight in weights.values()):
            raise ValueError("scoring weights must be between 0 and 1")
        if abs(sum(weights.values()) - 1) > 1e-9:
            raise ValueError("scoring weights must sum to 1")
        policy[entity_type] = dict(weights)
    return policy


def _merge_candidates(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    merged: dict[object, Candidate] = {}
    for candidate in candidates:
        existing = merged.get(candidate.entity_id)
        if existing is None:
            merged[candidate.entity_id] = candidate
            continue
        merged[candidate.entity_id] = Candidate(
            entity=candidate.entity,
            block_key=candidate.block_key,
            lexical_score=max_optional(existing.lexical_score, candidate.lexical_score),
            vector_score=max_optional(existing.vector_score, candidate.vector_score),
            retrieval_scope=(
                "strict"
                if "strict" in {existing.retrieval_scope, candidate.retrieval_scope}
                else "relaxed"
            ),
        )
    return tuple(merged.values())


def max_optional(left: float | None, right: float | None) -> float | None:
    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None


def _feature_evidence(
    feature: str,
    source: NormalizedRecord,
    target: NormalizedRecord,
) -> MatchEvidence:
    source_value, target_value = _feature_values(feature, source, target)
    if feature in {"name", "path"}:
        score = _similarity(source_value, target_value)
    else:
        score = float(
            source_value is not None and target_value is not None and source_value == target_value
        )
    return MatchEvidence(
        feature=feature,
        source_value=source_value,
        target_value=target_value,
        score=score,
    )


def _feature_values(
    feature: str,
    source: NormalizedRecord,
    target: NormalizedRecord,
) -> tuple[str | None, str | None]:
    field = {
        "name": "display_name",
        "path": "organization_path",
        "parent": "parent_mapping_id",
        "class": "parent_mapping_id",
        "member": "member_mapping_id",
        "container": "container_mapping_id",
    }.get(feature, feature)
    if field == "parent_mapping_id":
        return _uuid_string(source.parent_mapping_id), _uuid_string(target.parent_mapping_id)
    return source.values.get(field), target.values.get(field)


def _similarity(left: str | None, right: str | None) -> float:
    if left is None or right is None:
        return 0
    return round(fuzz.WRatio(left, right) / 100, 6)


def _uuid_string(value: object | None) -> str | None:
    return str(value) if value is not None else None

from uuid import uuid4

import pytest

from app.matching.blocking import block_key
from app.matching.scorer import SCORE_POLICY_V1, CandidateScorer
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import Candidate, MatchStatus, NormalizedRecord


def record(source_id: str, name: str, parent_mapping_id=None) -> NormalizedRecord:
    return NormalizedRecord(
        entity_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        entity_type=EntityType.ORGANIZATION_UNIT,
        source_id=source_id,
        values={
            "name": name,
            "display_name": name,
            "organization_path": f"本部/{name}",
        },
        parent_mapping_id=parent_mapping_id,
        rule_version="normalization-v1",
    )


def candidate(entity: NormalizedRecord, score: float = 1) -> Candidate:
    return Candidate(
        entity=entity,
        block_key=block_key(entity),
        lexical_score=score,
    )


def test_clear_top_candidate_is_accepted_with_feature_evidence() -> None:
    parent = uuid4()
    source = record("source", "教务处", parent)
    target = record("target", "教务处", parent)

    decision = CandidateScorer().decide(source, [candidate(target)])

    assert decision.status is MatchStatus.ACCEPTED
    assert decision.target_entity_id == target.entity_id
    assert {"name", "path", "parent"} <= {item.feature for item in decision.evidence}


def test_close_top_two_candidates_require_review() -> None:
    parent = uuid4()
    source = record("source", "教务处", parent)
    first = record("target-1", "教务处", parent)
    second = record("target-2", "教务处", parent)

    decision = CandidateScorer().decide(source, [candidate(first), candidate(second)])

    assert decision.status is MatchStatus.MANUAL_REVIEW
    assert decision.confidence == 1
    evidence = {item.feature: item for item in decision.evidence}
    assert evidence["runner_up_score"].score == 1
    assert evidence["score_margin"].score == 0
    assert evidence["required_margin"].score == 0.08


def test_no_candidates_is_unmatched() -> None:
    decision = CandidateScorer().decide(record("source", "教务处"), [])

    assert decision.status is MatchStatus.UNMATCHED
    assert decision.target_entity_id is None


def test_custom_scoring_rules_require_distinct_provenance() -> None:
    with pytest.raises(ValueError, match="rule_version"):
        CandidateScorer(threshold=0.9)

    scorer = CandidateScorer(threshold=0.9, rule_version="scoring-strict-v2")
    source = record("source", "教务处", uuid4())
    target = record("target", "教务处", source.parent_mapping_id)

    assert scorer.decide(source, [candidate(target)]).rule_version == "scoring-strict-v2"


def test_custom_scoring_policy_must_be_complete_and_normalized() -> None:
    with pytest.raises(ValueError, match="features"):
        CandidateScorer(
            policy={EntityType.TEACHER: {"name": 1}},
            rule_version="invalid-v2",
        )
    invalid_total = dict(SCORE_POLICY_V1[EntityType.TEACHER])
    invalid_total["name"] = 0.9
    with pytest.raises(ValueError, match="sum to 1"):
        CandidateScorer(
            policy={EntityType.TEACHER: invalid_total},
            rule_version="invalid-v3",
        )

    valid_teacher = dict(SCORE_POLICY_V1[EntityType.TEACHER])
    scorer = CandidateScorer(
        policy={EntityType.TEACHER: valid_teacher},
        rule_version="teacher-policy-v2",
    )
    source = record("source", "教务处", uuid4())
    target = record("target", "教务处", source.parent_mapping_id)
    assert scorer.decide(source, [candidate(target)]).status is MatchStatus.ACCEPTED


def test_relaxed_candidate_records_parent_risk_without_name_only_acceptance() -> None:
    def student(source_id: str) -> NormalizedRecord:
        return NormalizedRecord(
            entity_id=uuid4(),
            snapshot_id=uuid4(),
            tenant_id="school-1",
            entity_type=EntityType.STUDENT,
            source_id=source_id,
            values={"display_name": "王小明"},
            rule_version="normalization-v1",
        )

    source = student("source")
    target = student("target")
    relaxed = Candidate(
        entity=target,
        block_key=block_key(source),
        lexical_score=1,
        retrieval_scope="relaxed",
    )

    decision = CandidateScorer().decide(source, [relaxed])

    assert decision.status is MatchStatus.MANUAL_REVIEW
    assert any(
        item.feature == "retrieval_scope" and item.target_value == "relaxed"
        for item in decision.evidence
    )

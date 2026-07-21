from uuid import uuid4

from app.matching.conflict_resolver import ConflictResolver
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import MatchDecision, MatchMethod, MatchStatus


def decision(
    source_id,
    target_id,
    method=MatchMethod.SCORED,
    status=MatchStatus.ACCEPTED,
    confidence=0.92,
) -> MatchDecision:
    return MatchDecision(
        entity_type=EntityType.TEACHER,
        source_entity_id=source_id,
        source_key=f"teacher:{source_id}",
        target_entity_id=target_id,
        target_key=f"teacher:{target_id}",
        method=method,
        status=status,
        confidence=confidence,
        rule_version="scoring-v1",
    )


def test_competing_sources_do_not_share_target() -> None:
    target_id = uuid4()
    decisions = [decision(uuid4(), target_id), decision(uuid4(), target_id)]

    resolved = ConflictResolver().resolve(decisions)

    assert {item.status for item in resolved} == {MatchStatus.CONFLICT}


def test_confirmed_historical_mapping_outranks_new_scored_candidate() -> None:
    target_id = uuid4()
    historical = decision(uuid4(), target_id, MatchMethod.HISTORICAL)
    scored = decision(uuid4(), target_id, MatchMethod.SCORED)

    resolved = ConflictResolver().resolve([historical, scored])

    by_source = {item.source_entity_id: item for item in resolved}
    assert by_source[historical.source_entity_id].status is MatchStatus.ACCEPTED
    assert by_source[scored.source_entity_id].status is MatchStatus.CONFLICT


def test_manual_review_sources_competing_for_target_become_conflicts() -> None:
    target_id = uuid4()
    decisions = [
        decision(uuid4(), target_id, status=MatchStatus.MANUAL_REVIEW),
        decision(uuid4(), target_id, status=MatchStatus.MANUAL_REVIEW),
    ]

    resolved = ConflictResolver().resolve(decisions)

    assert {item.status for item in resolved} == {MatchStatus.CONFLICT}


def test_later_decision_cannot_consume_an_existing_conflict_target() -> None:
    target_id = uuid4()
    existing = decision(uuid4(), target_id, status=MatchStatus.CONFLICT)
    incoming = decision(uuid4(), target_id)

    resolved = ConflictResolver().resolve([existing, incoming])

    assert {item.status for item in resolved} == {MatchStatus.CONFLICT}


def test_higher_confidence_source_wins_competing_target() -> None:
    target_id = uuid4()
    stronger = decision(uuid4(), target_id, confidence=0.97)
    weaker = decision(uuid4(), target_id, confidence=0.88)

    resolved = ConflictResolver().resolve([weaker, stronger])

    by_source = {item.source_entity_id: item for item in resolved}
    assert by_source[stronger.source_entity_id].status is MatchStatus.ACCEPTED
    assert by_source[weaker.source_entity_id].status is MatchStatus.CONFLICT
    assert any(
        item.feature == "one_to_one_assignment"
        for item in by_source[weaker.source_entity_id].evidence
    )


def test_global_assignment_uses_alternative_edge_to_maximize_total_confidence() -> None:
    first_source = uuid4()
    second_source = uuid4()
    first_target = uuid4()
    second_target = uuid4()
    edges = [
        decision(first_source, first_target, confidence=0.90),
        decision(first_source, second_target, confidence=0.80),
        decision(second_source, first_target, confidence=0.85),
    ]

    resolved = ConflictResolver().resolve(edges)

    by_source = {item.source_entity_id: item for item in resolved}
    assert by_source[first_source].target_entity_id == second_target
    assert by_source[first_source].status is MatchStatus.ACCEPTED
    assert by_source[second_source].target_entity_id == first_target
    assert by_source[second_source].status is MatchStatus.ACCEPTED


def test_equal_confidence_assignment_is_deterministic() -> None:
    first_source, second_source = sorted((uuid4(), uuid4()), key=str)
    target_id = uuid4()
    edges = [
        decision(second_source, target_id, confidence=0.90),
        decision(first_source, target_id, confidence=0.90),
    ]

    forward = ConflictResolver().resolve(edges)
    reverse = ConflictResolver().resolve(list(reversed(edges)))

    assert {item.source_entity_id: item.status for item in forward} == {
        item.source_entity_id: item.status for item in reverse
    }
    assert {item.status for item in forward} == {MatchStatus.CONFLICT}

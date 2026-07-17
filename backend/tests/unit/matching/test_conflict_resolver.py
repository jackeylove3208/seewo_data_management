from uuid import uuid4

from app.matching.conflict_resolver import ConflictResolver
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import MatchDecision, MatchMethod, MatchStatus


def decision(
    source_id,
    target_id,
    method=MatchMethod.SCORED,
    status=MatchStatus.ACCEPTED,
) -> MatchDecision:
    return MatchDecision(
        entity_type=EntityType.TEACHER,
        source_entity_id=source_id,
        source_key=f"teacher:{source_id}",
        target_entity_id=target_id,
        target_key=f"teacher:{target_id}",
        method=method,
        status=status,
        confidence=0.92,
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

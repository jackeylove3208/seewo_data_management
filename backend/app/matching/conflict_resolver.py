from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from app.schemas.matching import (
    MatchDecision,
    MatchEvidence,
    MatchMethod,
    MatchStatus,
)

CARDINALITY_STATUSES = {
    MatchStatus.ACCEPTED,
    MatchStatus.MANUAL_REVIEW,
    MatchStatus.CONFLICT,
}


class ConflictResolver:
    def resolve(self, decisions: Sequence[MatchDecision]) -> list[MatchDecision]:
        groups: dict[UUID, list[MatchDecision]] = defaultdict(list)
        for decision in decisions:
            if decision.status in CARDINALITY_STATUSES and decision.target_entity_id is not None:
                groups[decision.target_entity_id].append(decision)

        replacements: dict[UUID, MatchDecision] = {}
        for group in groups.values():
            if len(group) < 2:
                continue
            historical = [
                decision for decision in group if decision.method is MatchMethod.HISTORICAL
            ]
            protected = historical[0] if len(historical) == 1 else None
            for decision in group:
                if decision is protected:
                    continue
                replacements[decision.source_entity_id] = _as_conflict(decision)
            if protected is None:
                for decision in group:
                    replacements[decision.source_entity_id] = _as_conflict(decision)

        return [replacements.get(item.source_entity_id, item) for item in decisions]


def _as_conflict(decision: MatchDecision) -> MatchDecision:
    evidence = (
        *decision.evidence,
        MatchEvidence(
            feature="target_cardinality",
            source_value=decision.source_key,
            target_value=decision.target_key,
            score=0,
        ),
    )
    return decision.model_copy(update={"status": MatchStatus.CONFLICT, "evidence": evidence})

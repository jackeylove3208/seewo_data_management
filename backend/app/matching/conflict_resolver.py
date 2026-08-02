from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from app.schemas.matching import (
    MatchDecision,
    MatchEvidence,
    MatchMethod,
    MatchStatus,
)


class ConflictResolver:
    def resolve(self, decisions: Sequence[MatchDecision]) -> list[MatchDecision]:
        by_source: dict[UUID, list[MatchDecision]] = defaultdict(list)
        source_order: list[UUID] = []
        for decision in decisions:
            if decision.source_entity_id not in by_source:
                source_order.append(decision.source_entity_id)
            by_source[decision.source_entity_id].append(decision)

        selected: dict[UUID, MatchDecision] = {}
        claimed_targets: set[UUID] = set()
        historical = sorted(
            (
                item
                for item in decisions
                if item.method is MatchMethod.HISTORICAL
                and item.status is MatchStatus.ACCEPTED
                and item.target_entity_id is not None
            ),
            key=lambda item: str(item.source_entity_id),
        )
        for item in historical:
            assert item.target_entity_id is not None
            if item.source_entity_id in selected:
                continue
            if item.target_entity_id in claimed_targets:
                selected[item.source_entity_id] = _as_conflict(item)
                continue
            selected[item.source_entity_id] = item
            claimed_targets.add(item.target_entity_id)

        blocked_targets = {
            item.target_entity_id
            for item in decisions
            if item.status is MatchStatus.CONFLICT and item.target_entity_id is not None
        }
        manual_groups: dict[UUID, int] = defaultdict(int)
        for item in decisions:
            if item.status is MatchStatus.MANUAL_REVIEW and item.target_entity_id is not None:
                manual_groups[item.target_entity_id] += 1
        blocked_targets.update(target for target, count in manual_groups.items() if count > 1)
        accepted_groups: dict[UUID, list[MatchDecision]] = defaultdict(list)
        for item in decisions:
            if (
                item.status is MatchStatus.ACCEPTED
                and item.method is not MatchMethod.HISTORICAL
                and item.target_entity_id is not None
            ):
                accepted_groups[item.target_entity_id].append(item)
        for target, group in accepted_groups.items():
            ranked = sorted((item.confidence for item in group), reverse=True)
            contender_sources = {item.source_entity_id for item in group}
            has_alternatives = any(
                len(
                    {
                        item.target_entity_id
                        for item in accepted_groups_for_source
                        if item.status is MatchStatus.ACCEPTED and item.target_entity_id is not None
                    }
                )
                > 1
                for source_id, accepted_groups_for_source in by_source.items()
                if source_id in contender_sources
            )
            if len(ranked) > 1 and ranked[0] == ranked[1] and not has_alternatives:
                blocked_targets.add(target)

        assignable = {
            source_id: [
                item
                for item in source_items
                if item.status is MatchStatus.ACCEPTED
                and item.method is not MatchMethod.HISTORICAL
                and item.target_entity_id is not None
                and item.target_entity_id not in claimed_targets
                and item.target_entity_id not in blocked_targets
            ]
            for source_id, source_items in by_source.items()
            if source_id not in selected
        }
        selected.update(_maximum_weight_assignment(assignable))

        result: list[MatchDecision] = []
        for source_id in source_order:
            winner = selected.get(source_id)
            if winner is not None:
                result.append(winner)
                continue
            candidates = by_source[source_id]
            preferred = min(
                candidates,
                key=lambda item: (
                    -item.confidence,
                    str(item.target_entity_id) if item.target_entity_id else "",
                ),
            )
            if (
                preferred.status is MatchStatus.ACCEPTED and preferred.target_entity_id is not None
            ) or preferred.target_entity_id in blocked_targets:
                preferred = _as_conflict(preferred)
            result.append(preferred)
        return result


def _as_conflict(decision: MatchDecision) -> MatchDecision:
    evidence = (
        *decision.evidence,
        MatchEvidence(
            feature="one_to_one_assignment",
            source_value=decision.source_key,
            target_value=decision.target_key,
            score=0,
        ),
    )
    return decision.model_copy(update={"status": MatchStatus.CONFLICT, "evidence": evidence})


def _maximum_weight_assignment(
    edges_by_source: dict[UUID, list[MatchDecision]],
) -> dict[UUID, MatchDecision]:
    sources = sorted((source for source, edges in edges_by_source.items() if edges), key=str)
    targets = sorted(
        {
            edge.target_entity_id
            for source in sources
            for edge in edges_by_source[source]
            if edge.target_entity_id is not None
        },
        key=str,
    )
    if not sources or not targets:
        return {}
    edge_lookup = {
        (source, edge.target_entity_id): edge
        for source in sources
        for edge in edges_by_source[source]
        if edge.target_entity_id is not None
    }
    column_count = len(targets) + len(sources)
    costs: list[list[float]] = []
    for row, source in enumerate(sources):
        real_costs = [
            2.0
            if (edge := edge_lookup.get((source, target))) is None
            else 1.0 - edge.confidence + row * 1e-9 + column * 1e-12
            for column, target in enumerate(targets)
        ]
        costs.append([*real_costs, *([1.0] * len(sources))])

    assignment = _hungarian(costs, column_count)
    selected: dict[UUID, MatchDecision] = {}
    for row, column in enumerate(assignment):
        if column >= len(targets):
            continue
        edge = edge_lookup.get((sources[row], targets[column]))
        if edge is not None:
            selected[sources[row]] = edge
    return selected


def _hungarian(costs: list[list[float]], column_count: int) -> list[int]:
    """Return minimum-cost column per row for a rectangular matrix."""
    row_count = len(costs)
    u = [0.0] * (row_count + 1)
    v = [0.0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    previous_column = [0] * (column_count + 1)
    for row in range(1, row_count + 1):
        matched_row[0] = row
        minimum = [float("inf")] * (column_count + 1)
        used = [False] * (column_count + 1)
        column = 0
        while True:
            used[column] = True
            current_row = matched_row[column]
            delta = float("inf")
            next_column = 0
            for candidate_column in range(1, column_count + 1):
                if used[candidate_column]:
                    continue
                reduced = (
                    costs[current_row - 1][candidate_column - 1]
                    - u[current_row]
                    - v[candidate_column]
                )
                if reduced < minimum[candidate_column]:
                    minimum[candidate_column] = reduced
                    previous_column[candidate_column] = column
                if minimum[candidate_column] < delta:
                    delta = minimum[candidate_column]
                    next_column = candidate_column
            for candidate_column in range(column_count + 1):
                if used[candidate_column]:
                    u[matched_row[candidate_column]] += delta
                    v[candidate_column] -= delta
                else:
                    minimum[candidate_column] -= delta
            column = next_column
            if matched_row[column] == 0:
                break
        while True:
            previous = previous_column[column]
            matched_row[column] = matched_row[previous]
            column = previous
            if column == 0:
                break
    assignment = [column_count] * row_count
    for column in range(1, column_count + 1):
        if matched_row[column] != 0:
            assignment[matched_row[column] - 1] = column - 1
    return assignment

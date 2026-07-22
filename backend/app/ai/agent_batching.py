"""Deterministic model-batch manifests for actionable new Agent work."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from app.schemas.agent_ingestion import AgentEntityKind


@dataclass(frozen=True)
class AgentAnalysisBatchManifest:
    entity_kind: AgentEntityKind
    work_item_ids: tuple[UUID, ...]


def partition_analysis_batches(
    items: Iterable[tuple[AgentEntityKind, UUID]],
    *,
    max_items: int = 50,
) -> tuple[AgentAnalysisBatchManifest, ...]:
    if not 1 <= max_items <= 50:
        raise ValueError("new Agent model batch size must be between 1 and 50")
    by_kind: dict[AgentEntityKind, list[UUID]] = defaultdict(list)
    for entity_kind, work_item_id in items:
        by_kind[entity_kind].append(work_item_id)
    manifests: list[AgentAnalysisBatchManifest] = []
    for entity_kind in AgentEntityKind:
        work_item_ids = by_kind[entity_kind]
        for start in range(0, len(work_item_ids), max_items):
            manifests.append(
                AgentAnalysisBatchManifest(
                    entity_kind=entity_kind,
                    work_item_ids=tuple(work_item_ids[start : start + max_items]),
                )
            )
    return tuple(manifests)

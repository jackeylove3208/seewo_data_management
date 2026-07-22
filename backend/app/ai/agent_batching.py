"""Deterministic model-batch manifests for actionable new Agent work."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_analysis import AgentModelBatchRecord, AgentWorkItemRecord
from app.models.agent_runtime import AgentRunRecord
from app.repositories.agent_analysis import AgentAnalysisRepository
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


class AgentBatchPlanner:
    """Materialize replay-safe model batches from actionable persisted work items."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = AgentAnalysisRepository(session)

    async def create_for_run(self, *, run_id: UUID) -> tuple[AgentModelBatchRecord, ...]:
        run = await self._session.get(AgentRunRecord, run_id)
        if run is None:
            raise LookupError(f"agent run not found: {run_id}")
        work_items = tuple(
            await self._session.scalars(
                select(AgentWorkItemRecord)
                .where(
                    AgentWorkItemRecord.run_id == run_id,
                    AgentWorkItemRecord.kind.not_in(("correct", "resolved")),
                )
                .order_by(AgentWorkItemRecord.entity_kind, AgentWorkItemRecord.id)
            )
        )
        manifests = partition_analysis_batches(
            (AgentEntityKind(item.entity_kind), item.id) for item in work_items
        )
        saved: list[AgentModelBatchRecord] = []
        for manifest in manifests:
            input_hash = hashlib.sha256(
                json.dumps(
                    {
                        "entity_kind": manifest.entity_kind.value,
                        "work_item_ids": [str(item_id) for item_id in manifest.work_item_ids],
                    },
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            saved.append(
                await self._repository.create_or_get_batch(
                    run_id=run.id,
                    task_id=run.task_id,
                    tenant_id=run.tenant_id,
                    entity_kind=manifest.entity_kind.value,
                    input_hash=input_hash,
                    work_item_ids=manifest.work_item_ids,
                )
            )
        return tuple(saved)

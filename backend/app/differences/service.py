from collections import Counter
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.differences.classifier import ComparableEntity, DifferenceContext
from app.differences.detector import DifferenceDetector, ResolvedMapping
from app.differences.field_policies import UNRESOLVED_RELATION
from app.models.mappings import EntityMapping
from app.models.snapshots import CanonicalEntityRecord
from app.normalization.pipeline import NormalizationPipeline
from app.repositories.differences import DifferenceRepository
from app.repositories.snapshots import SnapshotRepository
from app.repositories.tasks import TaskRepository
from app.schemas.canonical_entities import (
    CanonicalEntity,
    EntityType,
    SourceRole,
    member_entity_types_for_role,
)
from app.schemas.differences import DifferenceSummary, DifferenceType
from app.schemas.ingestion import SnapshotMode
from app.schemas.matching import MatchEvidence, MatchStatus

_CANONICAL_ADAPTER: TypeAdapter[CanonicalEntity] = TypeAdapter(CanonicalEntity)


class DifferenceDetectionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        normalization: NormalizationPipeline | None = None,
        detector: DifferenceDetector | None = None,
        repository: DifferenceRepository | None = None,
    ) -> None:
        self.session = session
        self.normalization = normalization or NormalizationPipeline()
        self.detector = detector or DifferenceDetector()
        self.repository = repository or DifferenceRepository(session)
        self.tasks = TaskRepository(session)
        self.snapshots = SnapshotRepository(session)

    async def detect(self, task_id: UUID) -> DifferenceSummary:
        task = await self.tasks.get_for_update(task_id)
        if task is None:
            raise LookupError(f"reconciliation task not found: {task_id}")
        if task.stage not in {"matching", "differences_ready"}:
            raise ValueError("difference detection requires a completed matching stage")
        source_snapshot = await self.snapshots.get_for_task_role(
            task_id,
            SourceRole.AUTHORITATIVE,
        )
        target_snapshot = await self.snapshots.get_for_task_role(task_id, SourceRole.TARGET)
        if source_snapshot is None or target_snapshot is None:
            raise ValueError("difference detection requires a published snapshot pair")

        source = await self._load_entities(source_snapshot.id)
        target = await self._load_entities(target_snapshot.id)
        mappings = await self._load_mappings(
            task_id,
            source_snapshot.id,
            target_snapshot.id,
        )
        source, target = _add_relationship_context(source, target, mappings)
        context = DifferenceContext(
            task_id=task.id,
            tenant_id=task.tenant_id,
            source_snapshot_id=source_snapshot.id,
            target_snapshot_id=target_snapshot.id,
        )
        batch = self.detector.detect(
            context,
            source,
            target,
            mappings,
            SnapshotMode(task.snapshot_mode),
        )
        await self.repository.insert_many(batch.drafts)
        items = await self.repository.for_task(task_id)
        task.stage = "differences_ready"
        task.status = "ready"
        await self.session.flush()
        counts = Counter(item.difference_type for item in items)
        return DifferenceSummary(
            task_id=task_id,
            difference_ids=tuple(item.id for item in items),
            counts={difference_type: counts[difference_type] for difference_type in DifferenceType},
            processed_entities=batch.processed_entities,
            compared_pairs=batch.compared_pairs,
        )

    async def _load_entities(self, snapshot_id: UUID) -> tuple[ComparableEntity, ...]:
        rows = await self.session.scalars(
            select(CanonicalEntityRecord)
            .where(CanonicalEntityRecord.snapshot_id == snapshot_id)
            .order_by(CanonicalEntityRecord.entity_type, CanonicalEntityRecord.raw_row_number)
        )
        entities: list[ComparableEntity] = []
        for row in rows:
            canonical = _CANONICAL_ADAPTER.validate_python(row.canonical_payload)
            normalized = self.normalization.normalize(canonical)
            entities.append(
                ComparableEntity(
                    id=row.id,
                    entity_type=canonical.entity_type,
                    source_id=normalized.normalized["source_id"] or canonical.source_id,
                    raw_row_number=row.raw_row_number,
                    payload=row.canonical_payload,
                    normalized=dict(normalized.normalized),
                    raw_payload=row.raw_payload,
                )
            )
        return tuple(entities)

    async def _load_mappings(
        self,
        task_id: UUID,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
    ) -> tuple[ResolvedMapping, ...]:
        rows = await self.session.scalars(
            select(EntityMapping)
            .where(
                EntityMapping.task_id == task_id,
                EntityMapping.source_snapshot_id == source_snapshot_id,
                EntityMapping.target_snapshot_id == target_snapshot_id,
            )
            .order_by(EntityMapping.created_at, EntityMapping.id)
        )
        latest: dict[UUID, ResolvedMapping] = {}
        for row in rows:
            latest[row.source_entity_id] = ResolvedMapping(
                id=row.id,
                source_entity_id=row.source_entity_id,
                target_entity_id=row.target_entity_id,
                status=MatchStatus(row.status),
                evidence=tuple(MatchEvidence.model_validate(item) for item in row.evidence),
            )
        return tuple(latest.values())


def _add_relationship_context(
    source: Sequence[ComparableEntity],
    target: Sequence[ComparableEntity],
    mappings: Sequence[ResolvedMapping],
) -> tuple[tuple[ComparableEntity, ...], tuple[ComparableEntity, ...]]:
    source_by_id = {entity.id: entity for entity in source}
    accepted = {
        _record_key(source_by_id[mapping.source_entity_id]): mapping.target_entity_id
        for mapping in mappings
        if mapping.status is MatchStatus.ACCEPTED
        and mapping.target_entity_id is not None
        and mapping.source_entity_id in source_by_id
    }
    target_ids = {_record_key(entity): entity.id for entity in target}
    return (
        tuple(_with_context(entity, accepted) for entity in source),
        tuple(_with_context(entity, target_ids) for entity in target),
    )


def _with_context(
    entity: ComparableEntity,
    lookup: dict[str, UUID],
) -> ComparableEntity:
    values: dict[str, Any] = dict(entity.normalized)
    parent_type = _parent_type(entity.entity_type)
    parent_source_id = values.get("parent_source_id")
    parent_id = (
        lookup.get(f"{parent_type.value}:{parent_source_id}")
        if parent_type is not None and isinstance(parent_source_id, str)
        else None
    )
    if entity.entity_type is EntityType.MEMBERSHIP:
        member_source_id = values.get("member_source_id")
        container_source_id = values.get("container_source_id")
        member_id = _related_target(
            member_source_id if isinstance(member_source_id, str) else None,
            member_entity_types_for_role(
                values.get("role") if isinstance(values.get("role"), str) else None
            ),
            lookup,
        )
        container_id = _related_target(
            container_source_id if isinstance(container_source_id, str) else None,
            (EntityType.ORGANIZATION_UNIT, EntityType.CLASS),
            lookup,
        )
        values["member_mapping_id"] = _relationship_value(member_source_id, member_id)
        values["container_mapping_id"] = _relationship_value(
            container_source_id,
            container_id,
        )
    else:
        values["parent_mapping_id"] = _relationship_value(parent_source_id, parent_id)
    return ComparableEntity(
        id=entity.id,
        entity_type=entity.entity_type,
        source_id=entity.source_id,
        raw_row_number=entity.raw_row_number,
        payload=entity.payload,
        normalized=values,
        raw_payload=entity.raw_payload,
    )


def _record_key(entity: ComparableEntity) -> str:
    return f"{entity.entity_type.value}:{entity.source_id}"


def _relationship_value(source_id: object | None, target_id: UUID | None) -> str | None:
    if target_id is not None:
        return str(target_id)
    return UNRESOLVED_RELATION if isinstance(source_id, str) else None


def _parent_type(entity_type: EntityType) -> EntityType | None:
    return {
        EntityType.ORGANIZATION_UNIT: EntityType.ORGANIZATION_UNIT,
        EntityType.CLASS: EntityType.ORGANIZATION_UNIT,
        EntityType.TEACHER: EntityType.ORGANIZATION_UNIT,
        EntityType.STUDENT: EntityType.CLASS,
    }.get(entity_type)


def _related_target(
    source_id: str | None,
    entity_types: Sequence[EntityType],
    lookup: dict[str, UUID],
) -> UUID | None:
    if source_id is None:
        return None
    matches = [
        target
        for entity_type in entity_types
        if (target := lookup.get(f"{entity_type.value}:{source_id}")) is not None
    ]
    return matches[0] if len(matches) == 1 else None

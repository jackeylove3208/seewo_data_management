from collections import Counter, defaultdict
from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tokenization import TaskTokenizationContext
from app.matching.blocking import block_key
from app.matching.candidate_retriever import CandidateRetriever
from app.matching.conflict_resolver import ConflictResolver
from app.matching.exact_matcher import ExactMatcher
from app.matching.scorer import CandidateScorer
from app.matching.vector_index import VectorIndex
from app.models.mappings import EntityMapping
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import CanonicalEntityRecord, Snapshot
from app.normalization.pipeline import NormalizationPipeline
from app.repositories.mappings import MappingRepository
from app.repositories.snapshots import SnapshotRepository
from app.repositories.tasks import TaskRepository
from app.schemas.canonical_entities import (
    CanonicalEntity,
    EntityType,
    SourceRole,
    member_entity_types_for_role,
)
from app.schemas.matching import (
    Candidate,
    MatchDecision,
    MatchEvidence,
    MatchMethod,
    MatchStatus,
    NormalizedRecord,
    ResolutionSummary,
    SnapshotPair,
)

RESOLUTION_ORDER = (
    EntityType.ORGANIZATION_UNIT,
    EntityType.CLASS,
    EntityType.TEACHER,
    EntityType.STUDENT,
    EntityType.MEMBERSHIP,
)

_CANONICAL_ADAPTER: TypeAdapter[CanonicalEntity] = TypeAdapter(CanonicalEntity)


class EntityResolutionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        normalization: NormalizationPipeline | None = None,
        exact_matcher: ExactMatcher | None = None,
        scorer: CandidateScorer | None = None,
        conflict_resolver: ConflictResolver | None = None,
        vector_index: VectorIndex | None = None,
        mapping_repository: MappingRepository | None = None,
        top_k: int = 20,
        rematching_top_k: int = 3,
        tokenization_secret: str | None = None,
    ) -> None:
        self.session = session
        self.normalization = normalization or NormalizationPipeline()
        self.exact_matcher = exact_matcher or ExactMatcher()
        self.scorer = scorer or CandidateScorer()
        self.conflict_resolver = conflict_resolver or ConflictResolver()
        self.vector_index = vector_index
        self.top_k = top_k
        self.rematching_top_k = rematching_top_k
        self.tokenization_secret = tokenization_secret
        self.mappings = mapping_repository or MappingRepository(session)
        self.snapshots = SnapshotRepository(session)
        self.tasks = TaskRepository(session)

    async def resolve_task(self, task_id: UUID) -> ResolutionSummary:
        task = await self.tasks.get(task_id)
        if task is None:
            raise LookupError(f"reconciliation task not found: {task_id}")
        source = await self.snapshots.get_for_task_role(task_id, SourceRole.AUTHORITATIVE)
        target = await self.snapshots.get_for_task_role(task_id, SourceRole.TARGET)
        if source is None or target is None:
            raise ValueError("entity resolution requires a published snapshot pair")
        return await self.resolve(
            SnapshotPair(
                task_id=task_id,
                tenant_id=task.tenant_id,
                source_snapshot_id=source.id,
                target_snapshot_id=target.id,
            )
        )

    async def resolve(self, pair: SnapshotPair) -> ResolutionSummary:
        task, source_snapshot, target_snapshot = await self._validate_pair(pair)
        source_records = await self._load_records(source_snapshot.id, SourceRole.AUTHORITATIVE)
        target_records = await self._load_records(target_snapshot.id, SourceRole.TARGET)
        persisted = await self._latest_decisions(pair)
        if task.stage in {"matching", "differences_ready"}:
            source_ids = {record.entity_id for record in source_records}
            decision_ids = {decision.source_entity_id for decision in persisted}
            if decision_ids != source_ids:
                raise ValueError("persisted entity resolution decision set is incomplete")
            return _resolution_summary(pair, persisted)

        task.status = "processing"
        task.stage = "matching"
        source_by_type = _by_type(source_records)
        target_by_type = _by_type(target_records)
        target_ids = {record.record_key: record.entity_id for record in target_records}
        accepted_targets: dict[str, UUID] = {}
        decisions: list[MatchDecision] = []
        tokenization_context = (
            TaskTokenizationContext(
                secret=self.tokenization_secret,
                tenant_id=pair.tenant_id,
                task_id=pair.task_id,
            )
            if self.tokenization_secret is not None
            else None
        )

        for entity_type in RESOLUTION_ORDER:
            type_sources = source_by_type[entity_type]
            historical_mappings = await self.mappings.find_confirmed_many(
                pair.tenant_id,
                [record.record_key for record in type_sources],
            )
            target_stage = recompute_descendant_context(
                target_by_type[entity_type],
                target_ids,
                authoritative=False,
            )
            type_decisions: list[MatchDecision] = []
            for source_batch in _source_batches(entity_type, type_sources):
                source_stage = recompute_descendant_context(
                    source_batch,
                    accepted_targets,
                    authoritative=True,
                )
                batch_decisions = await self._resolve_type(
                    source_stage,
                    target_stage,
                    historical_mappings,
                    pair=pair,
                    tokenization_context=tokenization_context,
                )
                type_decisions = self.conflict_resolver.resolve([*type_decisions, *batch_decisions])
                for source in source_by_type[entity_type]:
                    accepted_targets.pop(source.record_key, None)
                for decision in type_decisions:
                    if (
                        decision.status is MatchStatus.ACCEPTED
                        and decision.target_entity_id is not None
                    ):
                        accepted_targets[decision.source_key] = decision.target_entity_id

            for decision in type_decisions:
                await self.mappings.save_decision(
                    task_id=pair.task_id,
                    tenant_id=pair.tenant_id,
                    source_snapshot_id=pair.source_snapshot_id,
                    target_snapshot_id=pair.target_snapshot_id,
                    decision=decision,
                )
            decisions.extend(type_decisions)

        task.status = "ready"
        task.stage = "matching"
        await self.session.flush()
        return _resolution_summary(pair, decisions)

    async def _validate_pair(
        self,
        pair: SnapshotPair,
    ) -> tuple[ReconciliationTask, Snapshot, Snapshot]:
        task = await self.tasks.get_for_update(pair.task_id)
        source = await self.session.get(Snapshot, pair.source_snapshot_id)
        target = await self.session.get(Snapshot, pair.target_snapshot_id)
        if task is None or source is None or target is None:
            raise LookupError("reconciliation task or snapshot pair was not found")
        if source.state != "published" or target.state != "published":
            raise ValueError("entity resolution requires published snapshots")
        if source.task_id != pair.task_id or target.task_id != pair.task_id:
            raise ValueError("snapshot pair does not belong to the reconciliation task")
        if source.source_role != SourceRole.AUTHORITATIVE.value:
            raise ValueError("source snapshot must be authoritative")
        if target.source_role != SourceRole.TARGET.value:
            raise ValueError("target snapshot must be the governance target")
        if task.tenant_id != pair.tenant_id:
            raise ValueError("snapshot pair tenant does not match the task")
        return task, source, target

    async def _latest_decisions(self, pair: SnapshotPair) -> tuple[MatchDecision, ...]:
        rows = await self.session.scalars(
            select(EntityMapping)
            .where(
                EntityMapping.task_id == pair.task_id,
                EntityMapping.source_snapshot_id == pair.source_snapshot_id,
                EntityMapping.target_snapshot_id == pair.target_snapshot_id,
            )
            .order_by(EntityMapping.created_at, EntityMapping.id)
        )
        latest: dict[UUID, MatchDecision] = {}
        for row in rows:
            latest[row.source_entity_id] = _decision_from_record(row)
        return tuple(latest.values())

    async def _load_records(
        self,
        snapshot_id: UUID,
        expected_role: SourceRole,
    ) -> tuple[NormalizedRecord, ...]:
        rows = await self.session.scalars(
            select(CanonicalEntityRecord)
            .where(CanonicalEntityRecord.snapshot_id == snapshot_id)
            .order_by(CanonicalEntityRecord.entity_type, CanonicalEntityRecord.raw_row_number)
        )
        records: list[NormalizedRecord] = []
        for row in rows:
            entity = _CANONICAL_ADAPTER.validate_python(row.canonical_payload)
            if entity.source_role is not expected_role:
                raise ValueError("canonical entity source role does not match its snapshot")
            normalized = self.normalization.normalize(entity)
            normalized_source_id = normalized.normalized.get("source_id")
            if normalized_source_id is None:
                raise ValueError("normalized canonical entity is missing source_id")
            records.append(
                NormalizedRecord(
                    entity_id=row.id,
                    snapshot_id=snapshot_id,
                    tenant_id=entity.tenant_id,
                    entity_type=entity.entity_type,
                    source_id=normalized_source_id,
                    values=normalized.normalized,
                    rule_version=normalized.rule_version,
                )
            )
        return tuple(records)

    async def _resolve_type(
        self,
        sources: Sequence[NormalizedRecord],
        targets: Sequence[NormalizedRecord],
        historical_mappings: dict[str, EntityMapping],
        *,
        pair: SnapshotPair,
        tokenization_context: TaskTokenizationContext | None,
    ) -> list[MatchDecision]:
        retriever = CandidateRetriever(targets)
        if self.vector_index is not None:
            await self.vector_index.upsert_snapshot(
                sources,
                SourceRole.AUTHORITATIVE,
                tokenization_context,
            )
            await self.vector_index.upsert_snapshot(
                targets,
                SourceRole.TARGET,
                tokenization_context,
            )
        decisions: list[MatchDecision] = []
        targets_by_key = {record.record_key: record for record in targets}
        exact_index = self.exact_matcher.build_index(targets)
        for source in sources:
            historical = historical_mappings.get(source.record_key)
            decision = _historical_decision(source, historical, targets_by_key)
            if decision is None:
                decision = self.exact_matcher.match(source, exact_index)
            if decision is None:
                candidates = retriever.lexical(source, top_k=self.top_k)
                if self.vector_index is not None:
                    candidates.extend(
                        await self.vector_index.search_opposite(
                            source,
                            SourceRole.AUTHORITATIVE,
                            source_snapshot_id=pair.source_snapshot_id,
                            target_snapshot_id=pair.target_snapshot_id,
                            top_k=self.rematching_top_k,
                            tokenization_context=tokenization_context,
                        )
                    )
                decision = self.scorer.decide(source, candidates)
            decisions.append(decision)
        if self.vector_index is None:
            return decisions

        unresolved = [
            source
            for source, decision in zip(sources, decisions, strict=True)
            if decision.status is not MatchStatus.ACCEPTED
        ]
        consumed_target_ids = {
            decision.target_entity_id
            for decision in decisions
            if decision.status is MatchStatus.ACCEPTED and decision.target_entity_id is not None
        }
        unconsumed_targets = [
            target for target in targets if target.entity_id not in consumed_target_ids
        ]
        if not unresolved and not unconsumed_targets:
            return decisions
        edges = await self.vector_index.bidirectional_edges(
            unresolved,
            unconsumed_targets,
            source_snapshot_id=pair.source_snapshot_id,
            target_snapshot_id=pair.target_snapshot_id,
            top_k=self.rematching_top_k,
            tokenization_context=tokenization_context,
        )
        targets_by_id = {target.entity_id: target for target in targets}
        edges_by_source: dict[UUID, list[Candidate]] = defaultdict(list)
        for edge in edges:
            target = targets_by_id.get(edge.target_entity_id)
            if target is None:
                continue
            edges_by_source[edge.source_entity_id].append(
                Candidate(
                    entity=target,
                    block_key=block_key(target),
                    vector_score=edge.vector_score,
                    retrieval_scope="relaxed",
                )
            )
        rescored: list[MatchDecision] = []
        sources_by_id = {source.entity_id: source for source in sources}
        for decision in decisions:
            reverse_candidates = edges_by_source.get(decision.source_entity_id)
            if decision.status is MatchStatus.ACCEPTED or not reverse_candidates:
                rescored.append(decision)
                continue
            rescored.append(
                self.scorer.decide(sources_by_id[decision.source_entity_id], reverse_candidates)
            )
        return self.conflict_resolver.resolve(rescored)


def _by_type(
    records: Sequence[NormalizedRecord],
) -> dict[EntityType, tuple[NormalizedRecord, ...]]:
    grouped: dict[EntityType, list[NormalizedRecord]] = defaultdict(list)
    for record in records:
        grouped[record.entity_type].append(record)
    return {entity_type: tuple(grouped[entity_type]) for entity_type in EntityType}


def _source_batches(
    entity_type: EntityType,
    records: Sequence[NormalizedRecord],
) -> tuple[tuple[NormalizedRecord, ...], ...]:
    if entity_type is not EntityType.ORGANIZATION_UNIT:
        return (tuple(records),)
    by_source_id = {record.source_id: record for record in records}
    remaining = dict(by_source_id)
    resolved: set[str] = set()
    batches: list[tuple[NormalizedRecord, ...]] = []
    while remaining:
        ready = tuple(
            record
            for source_id, record in sorted(remaining.items())
            if (parent_id := record.values.get("parent_source_id")) is None
            or parent_id in resolved
            or parent_id not in by_source_id
        )
        if not ready:
            raise ValueError(
                "organization hierarchy cannot be resolved because it contains a cycle"
            )
        batches.append(ready)
        for record in ready:
            remaining.pop(record.source_id)
            resolved.add(record.source_id)
    return tuple(batches)


def _with_context(
    record: NormalizedRecord,
    accepted_targets: dict[str, UUID],
    target_ids: dict[str, UUID],
    *,
    authoritative: bool,
) -> NormalizedRecord:
    values = dict(record.values)
    lookup = accepted_targets if authoritative else target_ids
    parent_type = _parent_type(record.entity_type)
    parent_source_id = values.get("parent_source_id")
    parent_mapping_id = (
        lookup.get(f"{parent_type.value}:{parent_source_id}")
        if parent_type is not None and parent_source_id is not None
        else None
    )
    if record.entity_type is EntityType.MEMBERSHIP:
        member_id = _related_target(
            values.get("member_source_id"),
            member_entity_types_for_role(values.get("role")),
            lookup,
        )
        container_id = _related_target(
            values.get("container_source_id"),
            (EntityType.ORGANIZATION_UNIT, EntityType.CLASS),
            lookup,
        )
        values["member_mapping_id"] = str(member_id) if member_id else None
        values["container_mapping_id"] = str(container_id) if container_id else None
        parent_mapping_id = container_id
    return record.model_copy(update={"values": values, "parent_mapping_id": parent_mapping_id})


def recompute_descendant_context(
    records: Sequence[NormalizedRecord],
    resolved_targets: dict[str, UUID],
    *,
    authoritative: bool,
) -> tuple[NormalizedRecord, ...]:
    """Rebuild relationship context after a parent mapping changes."""
    return tuple(
        _with_context(
            record,
            resolved_targets,
            resolved_targets,
            authoritative=authoritative,
        )
        for record in sorted(
            records,
            key=lambda item: (RESOLUTION_ORDER.index(item.entity_type), item.source_id),
        )
    )


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
    matches: list[UUID] = []
    for entity_type in entity_types:
        target = lookup.get(f"{entity_type.value}:{source_id}")
        if target is not None:
            matches.append(target)
    return matches[0] if len(matches) == 1 else None


def _historical_decision(
    source: NormalizedRecord,
    mapping: EntityMapping | None,
    targets_by_key: dict[str, NormalizedRecord],
) -> MatchDecision | None:
    if mapping is None or mapping.target_key is None:
        return None
    target = targets_by_key.get(mapping.target_key)
    if target is None:
        return None
    evidence = (
        MatchEvidence(
            feature="historical_mapping",
            source_value=str(mapping.id),
            target_value=mapping.target_key,
            score=1,
        ),
        MatchEvidence(
            feature="confirmed_by",
            source_value=mapping.confirmed_by,
            target_value=None,
            score=1,
        ),
        MatchEvidence(
            feature="original_rule_version",
            source_value=mapping.rule_version,
            target_value=None,
            score=1,
        ),
    )
    return MatchDecision(
        entity_type=source.entity_type,
        source_entity_id=source.entity_id,
        source_key=source.record_key,
        target_entity_id=target.entity_id,
        target_key=target.record_key,
        method=MatchMethod.HISTORICAL,
        status=MatchStatus.ACCEPTED,
        confidence=float(Decimal(mapping.confidence)),
        evidence=evidence,
        rule_version="historical-reuse-v1",
        confirmed_by=mapping.confirmed_by,
    )


def _decision_from_record(record: EntityMapping) -> MatchDecision:
    evidence = tuple(MatchEvidence.model_validate(item) for item in record.evidence)
    confirmed_by = record.confirmed_by
    if confirmed_by is None and record.method == MatchMethod.HISTORICAL.value:
        confirmed_by = next(
            (
                item.source_value
                for item in evidence
                if item.feature == "confirmed_by" and item.source_value is not None
            ),
            None,
        )
    return MatchDecision(
        entity_type=EntityType(record.entity_type),
        source_entity_id=record.source_entity_id,
        source_key=record.source_key,
        target_entity_id=record.target_entity_id,
        target_key=record.target_key,
        method=MatchMethod(record.method) if record.method else None,
        status=MatchStatus(record.status),
        confidence=float(record.confidence),
        evidence=evidence,
        rule_version=record.rule_version,
        confirmed_by=confirmed_by,
    )


def _resolution_summary(
    pair: SnapshotPair,
    decisions: Sequence[MatchDecision],
) -> ResolutionSummary:
    counts = Counter(decision.status for decision in decisions)
    return ResolutionSummary(
        task_id=pair.task_id,
        source_snapshot_id=pair.source_snapshot_id,
        target_snapshot_id=pair.target_snapshot_id,
        processed_entity_types=RESOLUTION_ORDER,
        decisions=tuple(decisions),
        counts={status: counts[status] for status in MatchStatus},
    )

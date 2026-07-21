from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.differences.classifier import (
    ComparableEntity,
    DifferenceClassifier,
    DifferenceContext,
    MatchedPair,
)
from app.differences.field_policies import FieldComparisonPolicy
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import DifferenceDraft
from app.schemas.ingestion import SnapshotMode
from app.schemas.matching import MatchEvidence, MatchMethod, MatchStatus


@dataclass(frozen=True)
class ResolvedMapping:
    id: UUID
    source_entity_id: UUID
    target_entity_id: UUID | None
    status: MatchStatus
    evidence: tuple[MatchEvidence, ...]
    entity_type: EntityType | None = None
    method: MatchMethod | None = None
    rule_version: str = "unknown"


@dataclass(frozen=True)
class DetectionBatch:
    drafts: tuple[DifferenceDraft, ...]
    processed_entities: int
    compared_pairs: int


class DifferenceDetector:
    def __init__(
        self,
        policy: FieldComparisonPolicy | None = None,
        classifier: DifferenceClassifier | None = None,
    ) -> None:
        self.policy = policy or FieldComparisonPolicy()
        self.classifier = classifier or DifferenceClassifier(rule_version=self.policy.version)

    def detect(
        self,
        context: DifferenceContext,
        source: Sequence[ComparableEntity],
        target: Sequence[ComparableEntity],
        mappings: Sequence[ResolvedMapping],
        mode: SnapshotMode,
    ) -> DetectionBatch:
        source_by_id = {entity.id: entity for entity in source}
        target_by_id = {entity.id: entity for entity in target}
        target_by_key = {_record_key(entity): entity.id for entity in target}
        mapping_by_source = {mapping.source_entity_id: mapping for mapping in mappings}
        duplicate_index = _build_duplicate_index(mappings, source_by_id, target)
        conflicts_by_target: dict[UUID, tuple[ComparableEntity, ...]] = {
            target_id: tuple(
                source_by_id[mapping.source_entity_id]
                for mapping in mappings
                if mapping.status is MatchStatus.CONFLICT
                and mapping.target_entity_id == target_id
                and mapping.source_entity_id in source_by_id
            )
            for target_id in {
                mapping.target_entity_id
                for mapping in mappings
                if mapping.status is MatchStatus.CONFLICT and mapping.target_entity_id is not None
            }
            if target_id is not None
        }
        reserved_targets = {
            mapping.target_entity_id
            for mapping in mappings
            if mapping.target_entity_id is not None
            and mapping.status
            in {MatchStatus.ACCEPTED, MatchStatus.MANUAL_REVIEW, MatchStatus.CONFLICT}
        }
        for candidate_ids in duplicate_index.values():
            reserved_targets.update(candidate_ids)
        for candidate_mapping in mappings:
            if candidate_mapping.status is MatchStatus.MANUAL_REVIEW:
                reserved_targets.update(_runner_up_ids(candidate_mapping, target_by_key))
        drafts: list[DifferenceDraft] = []
        compared_pairs = 0

        for source_entity in source:
            source_mapping = mapping_by_source.get(source_entity.id)
            if source_mapping is None or source_mapping.status is MatchStatus.UNMATCHED:
                drafts.append(
                    self.classifier.unmatched_source(
                        context,
                        source_entity,
                        mapping_id=source_mapping.id if source_mapping else None,
                        match_evidence=source_mapping.evidence if source_mapping else (),
                    )
                )
                continue
            if source_mapping.status is MatchStatus.MANUAL_REVIEW:
                continue
            if (
                source_mapping.status is MatchStatus.CONFLICT
                and source_mapping.target_entity_id is None
            ):
                candidate_ids = _duplicate_candidate_ids(
                    source_mapping,
                    source_entity,
                    duplicate_index,
                )
                drafts.append(
                    self.classifier.mapping_conflict(
                        context,
                        source_entity,
                        mapping_id=source_mapping.id,
                        match_evidence=source_mapping.evidence,
                        target_candidates=tuple(
                            target_by_id[target_id] for target_id in sorted(candidate_ids, key=str)
                        ),
                    )
                )
                continue
            target_entity = (
                target_by_id.get(source_mapping.target_entity_id)
                if source_mapping.target_entity_id is not None
                else None
            )
            if target_entity is None:
                if source_mapping.status is MatchStatus.ACCEPTED:
                    raise ValueError("accepted mapping references a missing target entity")
                continue
            pair = MatchedPair(
                source=source_entity,
                target=target_entity,
                mapping_id=source_mapping.id,
                match_evidence=source_mapping.evidence,
            )
            if source_mapping.status is MatchStatus.CONFLICT:
                competitors = tuple(
                    entity
                    for entity in conflicts_by_target.get(target_entity.id, ())
                    if entity.id != source_entity.id
                )
                drafts.append(
                    self.classifier.mapping_conflict(
                        context,
                        source_entity,
                        mapping_id=source_mapping.id,
                        match_evidence=source_mapping.evidence,
                        target=target_entity,
                        competing_sources=competitors,
                    )
                )
                continue
            fields = self.policy.compare(
                source_entity.entity_type,
                source_entity.normalized,
                target_entity.normalized,
                source_raw=source_entity.raw_payload or source_entity.payload,
                target_raw=target_entity.raw_payload or target_entity.payload,
            )
            compared_pairs += 1
            if difference := self.classifier.matched(context, pair, fields=fields):
                drafts.append(difference)

        for target_entity in target:
            if target_entity.id in reserved_targets:
                continue
            if difference := self.classifier.unmatched_target(context, target_entity, mode):
                drafts.append(difference)

        return DetectionBatch(
            drafts=tuple(drafts),
            processed_entities=len(source) + len(target),
            compared_pairs=compared_pairs,
        )


def _record_key(entity: ComparableEntity) -> str:
    return f"{entity.entity_type.value}:{entity.source_id}"


def _runner_up_ids(
    mapping: ResolvedMapping,
    target_by_key: dict[str, UUID],
) -> set[UUID]:
    return {
        target_id
        for evidence in mapping.evidence
        if evidence.feature == "runner_up_score" and evidence.target_value is not None
        if (target_id := target_by_key.get(evidence.target_value)) is not None
    }


def _build_duplicate_index(
    mappings: Sequence[ResolvedMapping],
    source_by_id: dict[UUID, ComparableEntity],
    targets: Sequence[ComparableEntity],
) -> dict[tuple[EntityType, tuple[str, ...], tuple[str | None, ...]], set[UUID]]:
    requested_keys = {
        (source.entity_type, fields, expected)
        for mapping in mappings
        if mapping.status is MatchStatus.CONFLICT
        if (source := source_by_id.get(mapping.source_entity_id)) is not None
        for evidence in mapping.evidence
        if evidence.feature.startswith("duplicate:")
        for fields in (tuple(evidence.feature.removeprefix("duplicate:").split("+")),)
        for expected in (tuple(_matching_value(source, field) for field in fields),)
    }
    index: dict[
        tuple[EntityType, tuple[str, ...], tuple[str | None, ...]],
        set[UUID],
    ] = {}
    for target in targets:
        for entity_type, fields, expected in requested_keys:
            if target.entity_type is not entity_type:
                continue
            values = tuple(_matching_value(target, field) for field in fields)
            if values == expected:
                index.setdefault((entity_type, fields, expected), set()).add(target.id)
    return index


def _duplicate_candidate_ids(
    mapping: ResolvedMapping,
    source: ComparableEntity,
    index: dict[tuple[EntityType, tuple[str, ...], tuple[str | None, ...]], set[UUID]],
) -> set[UUID]:
    candidates: set[UUID] = set()
    for evidence in mapping.evidence:
        if not evidence.feature.startswith("duplicate:"):
            continue
        fields = tuple(evidence.feature.removeprefix("duplicate:").split("+"))
        expected = tuple(_matching_value(source, field) for field in fields)
        candidates.update(index.get((source.entity_type, fields, expected), ()))
    return candidates


def _matching_value(entity: ComparableEntity, field: str) -> str | None:
    value = entity.normalized.get(field)
    return str(value) if value is not None else None

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.schemas.canonical_entities import EntityType, SourceRole
from app.schemas.differences import (
    DifferenceAction,
    DifferenceDraft,
    DifferenceEntityReference,
    DifferenceEvidence,
    DifferenceType,
    FieldDifference,
)
from app.schemas.ingestion import SnapshotMode
from app.schemas.matching import MatchEvidence, MatchStatus


@dataclass(frozen=True)
class DifferenceContext:
    task_id: UUID
    tenant_id: str
    source_snapshot_id: UUID
    target_snapshot_id: UUID


@dataclass(frozen=True)
class ComparableEntity:
    id: UUID
    entity_type: EntityType
    source_id: str
    raw_row_number: int
    payload: dict[str, Any]
    normalized: dict[str, Any]
    raw_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class MatchedPair:
    source: ComparableEntity
    target: ComparableEntity
    mapping_id: UUID
    match_evidence: tuple[MatchEvidence, ...] = ()


class DifferenceClassifier:
    def __init__(self, *, rule_version: str = "comparison-v1") -> None:
        self.rule_version = rule_version

    def unmatched_source(
        self,
        context: DifferenceContext,
        source: ComparableEntity,
        *,
        mapping_id: UUID | None = None,
        match_evidence: tuple[MatchEvidence, ...] = (),
    ) -> DifferenceDraft:
        return DifferenceDraft(
            task_id=context.task_id,
            tenant_id=context.tenant_id,
            entity_type=source.entity_type,
            difference_type=DifferenceType.SEEWO_MISSING,
            proposed_action=DifferenceAction.CREATE,
            evidence=self._evidence(
                context,
                source=source,
                mapping_id=mapping_id,
                match_evidence=match_evidence,
            ),
        )

    def unmatched_target(
        self,
        context: DifferenceContext,
        target: ComparableEntity,
        mode: SnapshotMode,
    ) -> DifferenceDraft | None:
        if mode is not SnapshotMode.FULL:
            return None
        return DifferenceDraft(
            task_id=context.task_id,
            tenant_id=context.tenant_id,
            entity_type=target.entity_type,
            difference_type=DifferenceType.SEEWO_REDUNDANT,
            proposed_action=DifferenceAction.DISABLE,
            evidence=self._evidence(context, target=target),
        )

    def matched(
        self,
        context: DifferenceContext,
        pair: MatchedPair,
        *,
        fields: tuple[FieldDifference, ...],
    ) -> DifferenceDraft | None:
        if not fields:
            return None
        structural = any(field.comparison == "structure" for field in fields)
        return DifferenceDraft(
            task_id=context.task_id,
            tenant_id=context.tenant_id,
            entity_type=pair.source.entity_type,
            difference_type=(
                DifferenceType.STRUCTURE_CONFLICT
                if structural
                else DifferenceType.ATTRIBUTE_CONFLICT
            ),
            proposed_action=(DifferenceAction.MOVE if structural else DifferenceAction.UPDATE),
            evidence=self._evidence(context, pair=pair, fields=fields),
        )

    def mapping_conflict(
        self,
        context: DifferenceContext,
        source: ComparableEntity,
        *,
        mapping_id: UUID,
        match_evidence: tuple[MatchEvidence, ...],
        target: ComparableEntity | None = None,
        target_candidates: tuple[ComparableEntity, ...] = (),
        competing_sources: tuple[ComparableEntity, ...] = (),
    ) -> DifferenceDraft:
        conflict = next(
            (
                item
                for item in match_evidence
                if item.feature.startswith("duplicate:") or item.feature == "target_cardinality"
            ),
            None,
        )
        field = FieldDifference(
            field=conflict.feature if conflict else "target_cardinality",
            source_value=conflict.source_value if conflict else source.source_id,
            target_value=(
                conflict.target_value
                if conflict
                else target.source_id
                if target is not None
                else None
            ),
            normalized_source=conflict.source_value if conflict else source.source_id,
            normalized_target=(
                conflict.target_value
                if conflict
                else target.source_id
                if target is not None
                else None
            ),
            comparison="duplicate",
        )
        return DifferenceDraft(
            task_id=context.task_id,
            tenant_id=context.tenant_id,
            entity_type=source.entity_type,
            difference_type=DifferenceType.DUPLICATE_CONFLICT,
            proposed_action=DifferenceAction.MANUAL_REVIEW,
            evidence=self._evidence(
                context,
                source=source,
                target=target,
                mapping_id=mapping_id,
                match_evidence=match_evidence,
                fields=(field,),
                related_entities=(
                    *(_reference(candidate, SourceRole.TARGET) for candidate in target_candidates),
                    *(
                        _reference(competitor, SourceRole.AUTHORITATIVE)
                        for competitor in competing_sources
                    ),
                ),
            ),
        )

    @staticmethod
    def classify_mapping_status(status: MatchStatus) -> DifferenceType | None:
        return {
            MatchStatus.UNMATCHED: DifferenceType.SEEWO_MISSING,
            MatchStatus.CONFLICT: DifferenceType.DUPLICATE_CONFLICT,
        }.get(status)

    def _evidence(
        self,
        context: DifferenceContext,
        *,
        source: ComparableEntity | None = None,
        target: ComparableEntity | None = None,
        pair: MatchedPair | None = None,
        mapping_id: UUID | None = None,
        match_evidence: tuple[MatchEvidence, ...] = (),
        fields: tuple[FieldDifference, ...] = (),
        related_entities: tuple[DifferenceEntityReference, ...] = (),
    ) -> DifferenceEvidence:
        if pair is not None:
            source, target = pair.source, pair.target
        return DifferenceEvidence(
            source_snapshot_id=context.source_snapshot_id,
            target_snapshot_id=context.target_snapshot_id,
            source_entity_id=source.id if source else None,
            target_entity_id=target.id if target else None,
            mapping_id=pair.mapping_id if pair else mapping_id,
            fields=fields,
            match_evidence=pair.match_evidence if pair else match_evidence,
            raw_source_row=source.raw_row_number if source else None,
            raw_target_row=target.raw_row_number if target else None,
            source_payload=source.payload if source else None,
            target_payload=target.payload if target else None,
            raw_source_payload=source.raw_payload if source else None,
            raw_target_payload=target.raw_payload if target else None,
            related_entities=related_entities,
            comparison_rule_version=self.rule_version,
        )


def _reference(
    entity: ComparableEntity,
    source_role: SourceRole,
) -> DifferenceEntityReference:
    return DifferenceEntityReference(
        entity_id=entity.id,
        entity_type=entity.entity_type,
        source_role=source_role,
        source_id=entity.source_id,
        raw_row_number=entity.raw_row_number,
        payload=entity.payload,
        raw_payload=entity.raw_payload,
    )

from uuid import uuid4

from app.differences.classifier import (
    ComparableEntity,
    DifferenceClassifier,
    DifferenceContext,
    MatchedPair,
)
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import DifferenceAction, DifferenceType, FieldDifference
from app.schemas.ingestion import SnapshotMode
from app.schemas.matching import MatchEvidence, MatchStatus


def context() -> DifferenceContext:
    return DifferenceContext(
        task_id=uuid4(),
        tenant_id="school-1",
        source_snapshot_id=uuid4(),
        target_snapshot_id=uuid4(),
    )


def entity(entity_type: EntityType, *, source_id: str = "entity-1") -> ComparableEntity:
    return ComparableEntity(
        id=uuid4(),
        entity_type=entity_type,
        source_id=source_id,
        raw_row_number=1,
        payload={"source_id": source_id, "name": "测试实体"},
        normalized={"source_id": source_id, "display_name": "测试实体"},
    )


def matched_pair() -> MatchedPair:
    return MatchedPair(
        source=entity(EntityType.TEACHER, source_id="teacher-1"),
        target=entity(EntityType.TEACHER, source_id="seewo-teacher-1"),
        mapping_id=uuid4(),
        match_evidence=(
            MatchEvidence(
                feature="employee_number",
                source_value="E001",
                target_value="E001",
                score=1,
            ),
        ),
    )


def field(name: str, comparison: str) -> FieldDifference:
    return FieldDifference(
        field=name,
        source_value="source",
        target_value="target",
        normalized_source="source",
        normalized_target="target",
        comparison=comparison,
    )


def test_unmatched_source_is_seewo_missing() -> None:
    mapping_id = uuid4()
    item = DifferenceClassifier().unmatched_source(
        context(),
        entity(EntityType.TEACHER),
        mapping_id=mapping_id,
    )

    assert item.difference_type is DifferenceType.SEEWO_MISSING
    assert item.proposed_action is DifferenceAction.CREATE
    assert item.evidence.mapping_id == mapping_id


def test_full_scope_unmatched_target_is_seewo_redundant() -> None:
    item = DifferenceClassifier().unmatched_target(
        context(),
        entity(EntityType.STUDENT),
        SnapshotMode.FULL,
    )

    assert item is not None
    assert item.difference_type is DifferenceType.SEEWO_REDUNDANT
    assert item.proposed_action is DifferenceAction.DISABLE


def test_partial_scope_suppresses_redundant() -> None:
    assert (
        DifferenceClassifier().unmatched_target(
            context(),
            entity(EntityType.STUDENT),
            SnapshotMode.PARTIAL,
        )
        is None
    )


def test_structural_field_wins_over_attribute() -> None:
    item = DifferenceClassifier().matched(
        context(),
        matched_pair(),
        fields=(field("phone", "attribute"), field("parent_mapping_id", "structure")),
    )

    assert item is not None
    assert item.difference_type is DifferenceType.STRUCTURE_CONFLICT
    assert item.proposed_action is DifferenceAction.MOVE


def test_attribute_only_pair_is_attribute_conflict() -> None:
    item = DifferenceClassifier().matched(
        context(),
        matched_pair(),
        fields=(field("phone", "attribute"),),
    )

    assert item is not None
    assert item.difference_type is DifferenceType.ATTRIBUTE_CONFLICT
    assert item.proposed_action is DifferenceAction.UPDATE


def test_equal_matched_pair_has_no_difference() -> None:
    assert DifferenceClassifier().matched(context(), matched_pair(), fields=()) is None


def test_mapping_conflict_is_duplicate_conflict() -> None:
    pair = matched_pair()
    competitor = entity(EntityType.TEACHER, source_id="teacher-2")
    item = DifferenceClassifier().mapping_conflict(
        context(),
        pair.source,
        mapping_id=pair.mapping_id,
        match_evidence=pair.match_evidence,
        target=pair.target,
        competing_sources=(competitor,),
    )

    assert item.difference_type is DifferenceType.DUPLICATE_CONFLICT
    assert item.proposed_action is DifferenceAction.MANUAL_REVIEW
    assert item.evidence.fields[0].comparison == "duplicate"
    assert item.evidence.related_entities[0].entity_id == competitor.id
    assert item.evidence.related_entities[0].source_role.value == "authoritative"


def test_manual_review_mapping_is_not_a_difference() -> None:
    assert DifferenceClassifier().classify_mapping_status(MatchStatus.MANUAL_REVIEW) is None


def test_no_classifier_action_is_delete() -> None:
    classifier = DifferenceClassifier()
    examples = (
        classifier.unmatched_source(context(), entity(EntityType.TEACHER)),
        classifier.unmatched_target(context(), entity(EntityType.TEACHER), SnapshotMode.FULL),
        classifier.mapping_conflict(
            context(),
            matched_pair().source,
            mapping_id=uuid4(),
            match_evidence=(),
        ),
    )

    assert all(item is not None and item.proposed_action.value != "delete" for item in examples)

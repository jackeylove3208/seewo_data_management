import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.differences.classifier import ComparableEntity, DifferenceContext
from app.differences.detector import DifferenceDetector, ResolvedMapping
from app.schemas.canonical_entities import EntityType
from app.schemas.ingestion import SnapshotMode
from app.schemas.matching import MatchEvidence, MatchStatus

CASES = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "difference_cases.json").read_text(encoding="utf-8")
)


def comparable(entity_type: EntityType, values: dict, source_id: str) -> ComparableEntity:
    return ComparableEntity(
        id=uuid4(),
        entity_type=entity_type,
        source_id=source_id,
        raw_row_number=1,
        payload={"source_id": source_id, **values},
        normalized={"source_id": source_id, **values},
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_expected_difference_matrix(case: dict) -> None:
    entity_type = EntityType(case["entity_type"])
    source = (
        comparable(entity_type, case["source"], "source-1") if case["source"] is not None else None
    )
    target = (
        comparable(entity_type, case["target"], "target-1") if case["target"] is not None else None
    )
    mappings = ()
    if source is not None and case["mapping_status"] is not None:
        mappings = (
            ResolvedMapping(
                id=uuid4(),
                source_entity_id=source.id,
                target_entity_id=target.id if target else None,
                status=MatchStatus(case["mapping_status"]),
                evidence=(),
            ),
        )
    context = DifferenceContext(
        task_id=uuid4(),
        tenant_id="school-1",
        source_snapshot_id=uuid4(),
        target_snapshot_id=uuid4(),
    )

    result = DifferenceDetector().detect(
        context,
        (source,) if source else (),
        (target,) if target else (),
        mappings,
        SnapshotMode(case["mode"]),
    )

    assert [draft.difference_type.value for draft in result.drafts] == case["expected"]


def test_exact_key_conflict_without_target_id_reserves_duplicate_candidates() -> None:
    source = comparable(EntityType.TEACHER, {"employee_number": "E001"}, "source-1")
    targets = (
        comparable(EntityType.TEACHER, {"employee_number": "E001"}, "target-1"),
        comparable(EntityType.TEACHER, {"employee_number": "E001"}, "target-2"),
        comparable(EntityType.TEACHER, {"employee_number": "E999"}, "target-3"),
    )
    mapping = ResolvedMapping(
        id=uuid4(),
        source_entity_id=source.id,
        target_entity_id=None,
        status=MatchStatus.CONFLICT,
        evidence=(
            MatchEvidence(
                feature="duplicate:employee_number",
                source_value="E001",
                target_value="2 candidates",
                score=0,
            ),
        ),
    )

    result = DifferenceDetector().detect(
        DifferenceContext(
            task_id=uuid4(),
            tenant_id="school-1",
            source_snapshot_id=uuid4(),
            target_snapshot_id=uuid4(),
        ),
        (source,),
        targets,
        (mapping,),
        SnapshotMode.FULL,
    )

    assert [item.difference_type.value for item in result.drafts] == [
        "duplicate_conflict",
        "seewo_redundant",
    ]
    related = result.drafts[0].evidence.related_entities
    assert {item.entity_id for item in related} == {targets[0].id, targets[1].id}
    assert {item.source_role.value for item in related} == {"target"}


def test_exact_key_conflict_preserves_identifiers_containing_pipe() -> None:
    source = comparable(EntityType.TEACHER, {"employee_number": "E|001"}, "source-1")
    candidates = (
        comparable(EntityType.TEACHER, {"employee_number": "E|001"}, "target-1"),
        comparable(EntityType.TEACHER, {"employee_number": "E|001"}, "target-2"),
    )
    unrelated = comparable(EntityType.TEACHER, {"employee_number": "E999"}, "target-3")
    mapping = ResolvedMapping(
        id=uuid4(),
        source_entity_id=source.id,
        target_entity_id=None,
        status=MatchStatus.CONFLICT,
        evidence=(
            MatchEvidence(
                feature="duplicate:employee_number",
                source_value="E|001",
                target_value="2 candidates",
                score=0,
            ),
        ),
    )

    result = DifferenceDetector().detect(
        DifferenceContext(
            task_id=uuid4(),
            tenant_id="school-1",
            source_snapshot_id=uuid4(),
            target_snapshot_id=uuid4(),
        ),
        (source,),
        (*candidates, unrelated),
        (mapping,),
        SnapshotMode.FULL,
    )

    assert [item.difference_type.value for item in result.drafts] == [
        "duplicate_conflict",
        "seewo_redundant",
    ]
    assert {item.entity_id for item in result.drafts[0].evidence.related_entities} == {
        candidate.id for candidate in candidates
    }
    assert result.drafts[1].evidence.target_entity_id == unrelated.id


def test_manual_review_reserves_best_and_runner_up_targets() -> None:
    source = comparable(EntityType.TEACHER, {"name": "王伟"}, "source-1")
    best = comparable(EntityType.TEACHER, {"name": "王伟"}, "target-1")
    runner_up = comparable(EntityType.TEACHER, {"name": "王威"}, "target-2")
    another_candidate = comparable(EntityType.TEACHER, {"name": "王维"}, "target-3")
    mapping = ResolvedMapping(
        id=uuid4(),
        source_entity_id=source.id,
        target_entity_id=best.id,
        status=MatchStatus.MANUAL_REVIEW,
        evidence=(
            MatchEvidence(
                feature="runner_up_score",
                source_value=None,
                target_value=f"teacher:{runner_up.source_id}",
                score=0.8,
            ),
        ),
    )

    result = DifferenceDetector().detect(
        DifferenceContext(
            task_id=uuid4(),
            tenant_id="school-1",
            source_snapshot_id=uuid4(),
            target_snapshot_id=uuid4(),
        ),
        (source,),
        (best, runner_up, another_candidate),
        (mapping,),
        SnapshotMode.FULL,
    )

    assert [item.difference_type.value for item in result.drafts] == ["seewo_redundant"]
    assert result.drafts[0].evidence.target_entity_id == another_candidate.id

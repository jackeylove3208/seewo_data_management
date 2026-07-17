from uuid import uuid4

from app.differences.classifier import ComparableEntity, DifferenceContext
from app.differences.detector import DifferenceDetector, ResolvedMapping
from app.schemas.canonical_entities import EntityType
from app.schemas.ingestion import SnapshotMode
from app.schemas.matching import MatchStatus


def test_ten_thousand_matched_pairs_are_compared_once() -> None:
    source = []
    target = []
    mappings = []
    for index in range(10_000):
        source_entity = ComparableEntity(
            id=uuid4(),
            entity_type=EntityType.TEACHER,
            source_id=f"source-{index}",
            raw_row_number=index + 1,
            payload={"name": f"教师{index}"},
            normalized={"display_name": f"教师{index}"},
        )
        target_entity = ComparableEntity(
            id=uuid4(),
            entity_type=EntityType.TEACHER,
            source_id=f"target-{index}",
            raw_row_number=index + 1,
            payload={"name": f"教师{index}"},
            normalized={"display_name": f"教师{index}"},
        )
        source.append(source_entity)
        target.append(target_entity)
        mappings.append(
            ResolvedMapping(
                id=uuid4(),
                source_entity_id=source_entity.id,
                target_entity_id=target_entity.id,
                status=MatchStatus.ACCEPTED,
                evidence=(),
            )
        )

    result = DifferenceDetector().detect(
        DifferenceContext(
            task_id=uuid4(),
            tenant_id="school-1",
            source_snapshot_id=uuid4(),
            target_snapshot_id=uuid4(),
        ),
        source,
        target,
        mappings,
        SnapshotMode.FULL,
    )

    assert result.processed_entities == 20_000
    assert result.compared_pairs == 10_000
    assert result.drafts == ()

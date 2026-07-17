import pytest
from sqlalchemy import text

from app.differences.service import DifferenceDetectionService
from app.matching.service import EntityResolutionService
from app.models.reconciliation import ReconciliationTask
from app.repositories.differences import DifferenceRepository
from app.repositories.mappings import MappingRepository
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import DifferenceType
from app.schemas.matching import MatchDecision, MatchStatus
from tests.fixtures.organization_factory import create_hierarchy_pair


@pytest.mark.asyncio
async def test_detection_binds_snapshot_mapping_and_field_evidence(session) -> None:
    pair = await create_hierarchy_pair(session)
    await EntityResolutionService(session).resolve(pair)

    summary = await DifferenceDetectionService(session).detect(pair.task_id)
    items = await DifferenceRepository(session).for_task(pair.task_id)

    assert summary.difference_ids == tuple(item.id for item in items)
    assert summary.processed_entities == 12
    assert items
    assert all(item.evidence.source_snapshot_id == pair.source_snapshot_id for item in items)
    assert all(item.evidence.target_snapshot_id == pair.target_snapshot_id for item in items)
    assert all(item.evidence.comparison_rule_version == "comparison-v1" for item in items)
    matched = next(
        item
        for item in items
        if item.difference_type
        in {DifferenceType.ATTRIBUTE_CONFLICT, DifferenceType.STRUCTURE_CONFLICT}
    )
    assert matched.evidence.mapping_id is not None
    assert matched.evidence.fields
    assert matched.evidence.source_payload is not None
    assert matched.evidence.raw_source_payload is not None
    assert "id" in matched.evidence.raw_source_payload
    foreign_key_violations = (
        await session.execute(text("PRAGMA foreign_key_check(difference_items)"))
    ).all()
    assert foreign_key_violations == []


@pytest.mark.asyncio
async def test_retry_does_not_duplicate_differences(session) -> None:
    pair = await create_hierarchy_pair(session)
    await EntityResolutionService(session).resolve(pair)
    service = DifferenceDetectionService(session)

    first = await service.detect(pair.task_id)
    second = await service.detect(pair.task_id)

    assert second.difference_ids == first.difference_ids
    assert second.counts == first.counts


@pytest.mark.asyncio
async def test_resolution_and_detection_retry_do_not_append_differences(session) -> None:
    pair = await create_hierarchy_pair(session)
    resolution = EntityResolutionService(session)
    detection = DifferenceDetectionService(session)

    await resolution.resolve(pair)
    first = await detection.detect(pair.task_id)
    await resolution.resolve(pair)
    second = await detection.detect(pair.task_id)

    assert second.difference_ids == first.difference_ids
    assert second.counts == first.counts


@pytest.mark.asyncio
async def test_manual_review_is_not_reported_as_missing(session) -> None:
    pair = await create_hierarchy_pair(session)
    resolution = await EntityResolutionService(session).resolve(pair)
    assert any(
        decision.entity_type is EntityType.TEACHER and decision.status.value == "manual_review"
        for decision in resolution.decisions
    )

    await DifferenceDetectionService(session).detect(pair.task_id)
    items = await DifferenceRepository(session).for_task(pair.task_id)

    assert not any(
        item.entity_type is EntityType.TEACHER
        and item.difference_type is DifferenceType.SEEWO_MISSING
        for item in items
    )


@pytest.mark.asyncio
async def test_detection_marks_task_stage_ready(session) -> None:
    pair = await create_hierarchy_pair(session)
    await EntityResolutionService(session).resolve(pair)

    await DifferenceDetectionService(session).detect(pair.task_id)

    task = await session.get(ReconciliationTask, pair.task_id)
    assert task is not None
    assert task.status == "ready"
    assert task.stage == "differences_ready"


@pytest.mark.asyncio
async def test_new_mapping_decision_is_materialized_after_first_detection(session) -> None:
    pair = await create_hierarchy_pair(session)
    resolution = await EntityResolutionService(session).resolve(pair)
    manual = next(
        decision
        for decision in resolution.decisions
        if decision.status is MatchStatus.MANUAL_REVIEW
    )
    service = DifferenceDetectionService(session)
    first = await service.detect(pair.task_id)
    await MappingRepository(session).save_decision(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.source_snapshot_id,
        target_snapshot_id=pair.target_snapshot_id,
        decision=MatchDecision(
            entity_type=manual.entity_type,
            source_entity_id=manual.source_entity_id,
            source_key=manual.source_key,
            status=MatchStatus.UNMATCHED,
            confidence=0,
            rule_version="manual-rejection-v1",
        ),
    )
    await session.flush()

    second = await service.detect(pair.task_id)
    items = await DifferenceRepository(session).for_task(pair.task_id)

    assert len(second.difference_ids) == len(first.difference_ids) + 2
    assert any(
        item.evidence.mapping_id is not None
        and item.difference_type is DifferenceType.SEEWO_MISSING
        for item in items
    )
    assert any(
        item.entity_type is EntityType.TEACHER
        and item.difference_type is DifferenceType.SEEWO_REDUNDANT
        for item in items
    )

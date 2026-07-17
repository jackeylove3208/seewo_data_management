from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from app.matching.service import RESOLUTION_ORDER, EntityResolutionService, _with_context
from app.models.mappings import EntityMapping
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import CanonicalEntityRecord
from app.repositories.mappings import MappingRepository
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import (
    MatchDecision,
    MatchMethod,
    MatchStatus,
    NormalizedRecord,
)
from tests.fixtures.organization_factory import create_hierarchy_pair


@pytest.mark.asyncio
async def test_parent_mapping_becomes_teacher_evidence(session) -> None:
    pair = await create_hierarchy_pair(session)

    summary = await EntityResolutionService(session).resolve(pair)

    teacher = next(
        decision for decision in summary.decisions if decision.entity_type is EntityType.TEACHER
    )
    assert teacher.status is MatchStatus.MANUAL_REVIEW
    assert any(item.feature == "parent" and item.score == 1 for item in teacher.evidence)
    child_department = next(
        decision
        for decision in summary.decisions
        if decision.source_key == "organization_unit:D-CHILD"
    )
    assert child_department.status is MatchStatus.ACCEPTED
    assert any(item.feature == "parent" and item.score == 1 for item in child_department.evidence)
    assert summary.processed_entity_types == RESOLUTION_ORDER
    assert {decision.entity_type for decision in summary.decisions} == set(EntityType)
    assert await session.scalar(select(func.count()).select_from(EntityMapping)) == 6
    task = await session.get(ReconciliationTask, pair.task_id)
    assert task is not None
    assert task.stage == "matching"


@pytest.mark.asyncio
async def test_resolution_rejects_unpublished_snapshot(session) -> None:
    pair = await create_hierarchy_pair(session)
    from app.models.snapshots import Snapshot

    await session.execute(
        update(Snapshot).where(Snapshot.id == pair.source_snapshot_id).values(state="draft")
    )

    with pytest.raises(ValueError, match="published"):
        await EntityResolutionService(session).resolve(pair)


@pytest.mark.asyncio
async def test_historical_mappings_are_loaded_once_per_entity_type(
    session,
    monkeypatch,
) -> None:
    pair = await create_hierarchy_pair(session)
    repository = MappingRepository(session)
    calls: list[tuple[str, ...]] = []

    async def find_many(_tenant_id, source_keys):
        calls.append(tuple(source_keys))
        return {}

    monkeypatch.setattr(repository, "find_confirmed_many", find_many, raising=False)

    await EntityResolutionService(
        session,
        mapping_repository=repository,
    ).resolve(pair)

    assert len(calls) == len(EntityType)
    assert all(call for call in calls)


@pytest.mark.asyncio
async def test_confirmed_mapping_is_reused_by_resolution_service(session) -> None:
    pair = await create_hierarchy_pair(session)
    source = await session.scalar(
        select(CanonicalEntityRecord).where(
            CanonicalEntityRecord.snapshot_id == pair.source_snapshot_id,
            CanonicalEntityRecord.source_id == "t-a",
        )
    )
    target = await session.scalar(
        select(CanonicalEntityRecord).where(
            CanonicalEntityRecord.snapshot_id == pair.target_snapshot_id,
            CanonicalEntityRecord.source_id == "sw-t1",
        )
    )
    assert source is not None and target is not None
    repository = MappingRepository(session)
    await repository.confirm(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.source_snapshot_id,
        target_snapshot_id=pair.target_snapshot_id,
        decision=MatchDecision(
            entity_type=EntityType.TEACHER,
            source_entity_id=source.id,
            source_key="teacher:T-A",
            target_entity_id=target.id,
            target_key="teacher:SW-T1",
            method=MatchMethod.SCORED,
            status=MatchStatus.ACCEPTED,
            confidence=0.95,
            rule_version="manual-confirmation-v1",
        ),
        confirmed_by="operator-1",
    )

    summary = await EntityResolutionService(session).resolve(pair)
    teacher = next(
        decision for decision in summary.decisions if decision.entity_type is EntityType.TEACHER
    )

    assert teacher.method is MatchMethod.HISTORICAL
    assert teacher.confirmed_by == "operator-1"
    assert teacher.target_entity_id == target.id
    assert teacher.rule_version == "historical-reuse-v1"
    assert any(
        item.feature == "original_rule_version" and item.source_value == "manual-confirmation-v1"
        for item in teacher.evidence
    )


def test_membership_role_disambiguates_cross_type_member_ids() -> None:
    teacher_id = uuid4()
    student_id = uuid4()
    class_id = uuid4()
    record = NormalizedRecord(
        entity_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        entity_type=EntityType.MEMBERSHIP,
        source_id="M-1",
        values={
            "member_source_id": "MEMBER-1",
            "container_source_id": "CLASS-1",
            "role": "student",
        },
        rule_version="normalization-v1",
    )
    lookup = {
        "teacher:MEMBER-1": teacher_id,
        "student:MEMBER-1": student_id,
        "class:CLASS-1": class_id,
    }

    contextualized = _with_context(
        record,
        lookup,
        lookup,
        authoritative=True,
    )

    assert contextualized.values["member_mapping_id"] == str(student_id)

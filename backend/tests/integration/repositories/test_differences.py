from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select

from app.matching.service import EntityResolutionService
from app.models.differences import DifferenceRecord
from app.models.mappings import EntityMapping
from app.repositories.differences import DifferenceRepository, ImmutableDifferenceError
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import (
    DifferenceDraft,
    DifferenceEvidence,
    DifferenceStatus,
    DifferenceType,
    FieldDifference,
)
from tests.fixtures.organization_factory import create_hierarchy_pair


def evidence(**overrides: object) -> DifferenceEvidence:
    values = {
        "source_snapshot_id": uuid4(),
        "target_snapshot_id": uuid4(),
        "source_entity_id": uuid4(),
        "target_entity_id": uuid4(),
        "mapping_id": uuid4(),
        "fields": (
            FieldDifference(
                field="phone",
                source_value="13800000000",
                target_value="13900000000",
                normalized_source="13800000000",
                normalized_target="13900000000",
                comparison="attribute",
            ),
        ),
        "raw_source_row": 3,
        "raw_target_row": 7,
        "source_payload": {"name": "测试教师", "phone": "13800000000"},
        "target_payload": {"name": "测试教师", "phone": "13900000000"},
        "comparison_rule_version": "comparison-v1",
    }
    values.update(overrides)
    return DifferenceEvidence.model_validate(values)


def draft(**overrides: object) -> DifferenceDraft:
    values = {
        "task_id": uuid4(),
        "tenant_id": "school-1",
        "entity_type": EntityType.TEACHER,
        "difference_type": DifferenceType.ATTRIBUTE_CONFLICT,
        "proposed_action": "update",
        "evidence": evidence(),
    }
    values.update(overrides)
    return DifferenceDraft.model_validate(values)


@pytest.fixture
async def persisted_difference_parent(session) -> dict[str, object]:
    pair = await create_hierarchy_pair(session)
    await EntityResolutionService(session).resolve(pair)
    mapping = await session.scalar(
        select(EntityMapping).where(
            EntityMapping.task_id == pair.task_id,
            EntityMapping.status == "accepted",
            EntityMapping.target_entity_id.is_not(None),
        )
    )
    assert mapping is not None and mapping.target_entity_id is not None
    return {
        "task_id": pair.task_id,
        "tenant_id": pair.tenant_id,
        "entity_type": EntityType(mapping.entity_type),
        "evidence": evidence(
            source_snapshot_id=pair.source_snapshot_id,
            target_snapshot_id=pair.target_snapshot_id,
            source_entity_id=mapping.source_entity_id,
            target_entity_id=mapping.target_entity_id,
            mapping_id=mapping.id,
        ),
    }


def test_difference_evidence_requires_both_snapshot_ids() -> None:
    values = evidence().model_dump()
    values["target_snapshot_id"] = None

    with pytest.raises(ValidationError, match="target_snapshot_id"):
        DifferenceEvidence.model_validate(values)


@pytest.mark.asyncio
async def test_insert_persists_snapshot_bound_evidence(
    session,
    persisted_difference_parent,
) -> None:
    repository = DifferenceRepository(session)
    item = draft(**persisted_difference_parent)

    saved = (await repository.insert_many((item,)))[0]
    await session.flush()

    assert saved.task_id == item.task_id
    assert saved.status is DifferenceStatus.OPEN
    assert saved.evidence.source_snapshot_id == item.evidence.source_snapshot_id
    assert saved.evidence.target_snapshot_id == item.evidence.target_snapshot_id
    assert saved.evidence.fields[0].field == "phone"
    assert saved.version == 1


@pytest.mark.asyncio
async def test_insert_is_idempotent_for_the_same_evidence(
    session,
    persisted_difference_parent,
) -> None:
    repository = DifferenceRepository(session)
    item = draft(**persisted_difference_parent)

    first = (await repository.insert_many((item,)))[0]
    second = (await repository.insert_many((item,)))[0]
    await session.flush()

    assert second.id == first.id
    assert await session.scalar(select(func.count()).select_from(DifferenceRecord)) == 1


@pytest.mark.asyncio
async def test_duplicate_drafts_in_one_batch_return_the_same_item(
    session,
    persisted_difference_parent,
) -> None:
    repository = DifferenceRepository(session)
    item = draft(**persisted_difference_parent)

    first, second = await repository.insert_many((item, item))

    assert second.id == first.id
    assert await session.scalar(select(func.count()).select_from(DifferenceRecord)) == 1


@pytest.mark.asyncio
async def test_saved_difference_is_immutable(
    session,
    persisted_difference_parent,
) -> None:
    repository = DifferenceRepository(session)
    saved = (await repository.insert_many((draft(**persisted_difference_parent),)))[0]
    record = await session.get(DifferenceRecord, saved.id)
    assert record is not None

    record.proposed_action = "disable"
    with pytest.raises(ImmutableDifferenceError):
        await session.flush()


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_id(session) -> None:
    assert await DifferenceRepository(session).get(UUID(int=0)) is None


@pytest.mark.asyncio
async def test_bulk_insert_uses_constant_database_round_trips(
    session,
    database,
    persisted_difference_parent,
) -> None:
    statement_count = 0

    def count_statement(*_args) -> None:
        nonlocal statement_count
        statement_count += 1

    event.listen(database.engine.sync_engine, "before_cursor_execute", count_statement)
    common = draft(**persisted_difference_parent)
    drafts = tuple(
        common.model_copy(
            update={
                "evidence": common.evidence.model_copy(
                    update={
                        "raw_source_row": index + 1,
                        "source_payload": {"name": f"教师{index}"},
                    }
                )
            }
        )
        for index in range(100)
    )

    try:
        await DifferenceRepository(session).insert_many(drafts)
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", count_statement)

    assert statement_count <= 5

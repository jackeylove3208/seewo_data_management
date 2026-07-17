from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.mappings import EntityMapping
from app.models.snapshots import CanonicalEntityRecord
from app.repositories.mappings import MappingCardinalityError, MappingRepository
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import MatchDecision, MatchEvidence, MatchMethod, MatchStatus
from tests.fixtures.organization_factory import create_hierarchy_pair


def decision(
    *,
    source_key: str = "teacher:T001",
    source_entity_id=None,
    target_key: str = "teacher:SW001",
    target_entity_id=None,
) -> MatchDecision:
    return MatchDecision(
        entity_type=EntityType.TEACHER,
        source_entity_id=source_entity_id or uuid4(),
        source_key=source_key,
        target_entity_id=target_entity_id or uuid4(),
        target_key=target_key,
        method=MatchMethod.STABLE_ID,
        status=MatchStatus.ACCEPTED,
        confidence=1,
        evidence=(
            MatchEvidence(
                feature="employee_number",
                source_value="E007",
                target_value="E007",
                score=1,
            ),
        ),
        rule_version="matching-v1",
    )


@pytest.fixture
async def persisted_mapping_context(session):
    pair = await create_hierarchy_pair(session)
    source_teacher = await session.scalar(
        select(CanonicalEntityRecord).where(
            CanonicalEntityRecord.snapshot_id == pair.source_snapshot_id,
            CanonicalEntityRecord.entity_type == EntityType.TEACHER.value,
        )
    )
    target_teacher = await session.scalar(
        select(CanonicalEntityRecord).where(
            CanonicalEntityRecord.snapshot_id == pair.target_snapshot_id,
            CanonicalEntityRecord.entity_type == EntityType.TEACHER.value,
        )
    )
    assert source_teacher is not None and target_teacher is not None

    second_source = CanonicalEntityRecord(
        snapshot_id=pair.source_snapshot_id,
        entity_type=EntityType.TEACHER.value,
        source_id="t-b",
        raw_row_number=100,
        canonical_payload={"name": "李四"},
        raw_payload={"id": "t-b", "name": "李四"},
    )
    session.add(second_source)
    await session.flush()
    return pair, source_teacher, second_source, target_teacher


@pytest.mark.asyncio
async def test_confirmed_mapping_is_reused_until_revoked(
    session,
    persisted_mapping_context,
) -> None:
    repository = MappingRepository(session)
    snapshot_pair, source, _second_source, target = persisted_mapping_context
    pair = decision(source_entity_id=source.id, target_entity_id=target.id)

    saved = await repository.confirm(
        task_id=snapshot_pair.task_id,
        tenant_id="school-1",
        source_snapshot_id=snapshot_pair.source_snapshot_id,
        target_snapshot_id=snapshot_pair.target_snapshot_id,
        decision=pair,
        confirmed_by="operator-1",
    )
    same = await repository.confirm(
        task_id=snapshot_pair.task_id,
        tenant_id="school-1",
        source_snapshot_id=snapshot_pair.source_snapshot_id,
        target_snapshot_id=snapshot_pair.target_snapshot_id,
        decision=pair,
        confirmed_by="operator-1",
    )

    assert same.id == saved.id
    assert (await repository.find_confirmed("school-1", pair.source_key)).id == saved.id

    await repository.revoke(saved.id, revoked_by="operator-2", reason="wrong person")

    assert await repository.find_confirmed("school-1", pair.source_key) is None


@pytest.mark.asyncio
async def test_target_cannot_have_two_active_confirmed_sources(
    session,
    persisted_mapping_context,
) -> None:
    repository = MappingRepository(session)
    pair, first_source, second_source, target = persisted_mapping_context
    first = decision(source_entity_id=first_source.id, target_entity_id=target.id)
    second = decision(
        source_key="teacher:T002",
        source_entity_id=second_source.id,
        target_key=first.target_key or "teacher:SW001",
        target_entity_id=target.id,
    )
    common = {
        "task_id": pair.task_id,
        "tenant_id": "school-1",
        "source_snapshot_id": pair.source_snapshot_id,
        "target_snapshot_id": pair.target_snapshot_id,
        "confirmed_by": "operator-1",
    }

    await repository.confirm(decision=first, **common)
    with pytest.raises(MappingCardinalityError, match="target"):
        await repository.confirm(decision=second, **common)


@pytest.mark.asyncio
async def test_unconfirmed_decision_persists_evidence_and_provenance(
    session,
    persisted_mapping_context,
) -> None:
    repository = MappingRepository(session)
    snapshot_pair, source, _second_source, target = persisted_mapping_context
    pair = decision(source_entity_id=source.id, target_entity_id=target.id)

    saved = await repository.save_decision(
        task_id=snapshot_pair.task_id,
        tenant_id="school-1",
        source_snapshot_id=snapshot_pair.source_snapshot_id,
        target_snapshot_id=snapshot_pair.target_snapshot_id,
        decision=pair,
    )
    await session.flush()

    assert saved.method == "stable_id"
    assert saved.status == "accepted"
    assert saved.rule_version == "matching-v1"
    assert saved.evidence[0]["feature"] == "employee_number"
    assert saved.confirmed_by is None


@pytest.mark.asyncio
async def test_concurrent_confirmation_conflict_is_a_domain_error(
    session,
    monkeypatch,
    persisted_mapping_context,
) -> None:
    repository = MappingRepository(session)
    pair, first_source, second_source, target = persisted_mapping_context
    first = decision(source_entity_id=first_source.id, target_entity_id=target.id)
    second = decision(
        source_key="teacher:T002",
        source_entity_id=second_source.id,
        target_key=first.target_key or "teacher:SW001",
        target_entity_id=target.id,
    )
    common = {
        "task_id": pair.task_id,
        "tenant_id": "school-1",
        "source_snapshot_id": pair.source_snapshot_id,
        "target_snapshot_id": pair.target_snapshot_id,
        "confirmed_by": "operator-1",
    }
    await repository.confirm(decision=first, **common)

    async def no_active_mapping(*_args, **_kwargs):
        return None

    monkeypatch.setattr(repository, "find_confirmed", no_active_mapping)
    monkeypatch.setattr(repository, "_find_confirmed_target", no_active_mapping)

    with pytest.raises(MappingCardinalityError, match="concurrent"):
        await repository.confirm(decision=second, **common)

    assert await session.scalar(select(func.count()).select_from(EntityMapping)) == 1

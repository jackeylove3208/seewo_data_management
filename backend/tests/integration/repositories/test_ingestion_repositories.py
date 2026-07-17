from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.snapshots import CanonicalEntityRecord, RawSnapshotRow, Snapshot
from app.repositories.files import FileRepository
from app.repositories.snapshots import SnapshotDraft, SnapshotRepository
from app.repositories.tasks import TaskRepository
from app.schemas.canonical_entities import OrganizationUnit, SourceRole
from app.schemas.ingestion import IngestionSummary, SnapshotMode, SnapshotScope


async def create_task_and_files(session):
    tasks = TaskRepository(session)
    files = FileRepository(session)
    task = await tasks.create(
        scope=SnapshotScope(tenant_id="school-1", scope_id="all", mode=SnapshotMode.FULL),
        idempotency_key="task-1",
        request_hash="a" * 64,
    )
    source = await files.create(
        source_role=SourceRole.AUTHORITATIVE,
        original_name="third.csv",
        storage_name="source.csv",
        storage_path=Path("/tmp/source.csv"),
        sha256="b" * 64,
        size_bytes=100,
    )
    target = await files.create(
        source_role=SourceRole.TARGET,
        original_name="mofa.csv",
        storage_name="target.csv",
        storage_path=Path("/tmp/target.csv"),
        sha256="c" * 64,
        size_bytes=100,
    )
    await files.bind_to_task(source.id, task.id)
    await files.bind_to_task(target.id, task.id)
    await session.flush()
    return task, source, target


@pytest.mark.asyncio
async def test_task_idempotency_key_is_unique(session) -> None:
    repository = TaskRepository(session)
    scope = SnapshotScope(tenant_id="school-1", scope_id="all", mode=SnapshotMode.FULL)
    await repository.create(scope=scope, idempotency_key="same-key", request_hash="a" * 64)
    await session.flush()

    await repository.create(scope=scope, idempotency_key="same-key", request_hash="a" * 64)
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_task_allows_only_one_file_per_source_role(session) -> None:
    task, source, _ = await create_task_and_files(session)
    files = FileRepository(session)
    duplicate = await files.create(
        source_role=SourceRole.AUTHORITATIVE,
        original_name="second.csv",
        storage_name="second.csv",
        storage_path=Path("/tmp/second.csv"),
        sha256="d" * 64,
        size_bytes=100,
    )
    await files.bind_to_task(duplicate.id, task.id)

    with pytest.raises(IntegrityError):
        await session.flush()
    assert source.task_id == task.id


@pytest.mark.asyncio
async def test_snapshot_pair_persists_raw_and_canonical_provenance(session) -> None:
    task, source, target = await create_task_and_files(session)
    repository = SnapshotRepository(session)
    source_snapshot_id = uuid4()
    target_snapshot_id = uuid4()
    source_entity = OrganizationUnit(
        tenant_id="school-1",
        snapshot_id=source_snapshot_id,
        source_role=SourceRole.AUTHORITATIVE,
        source_id="D01",
        raw_row_number=2,
        raw_payload={"id": "D01", "name": "教务处"},
        name="教务处",
        code="D01",
    )
    target_entity = source_entity.model_copy(
        update={"snapshot_id": target_snapshot_id, "source_role": SourceRole.TARGET}
    )

    snapshots = await repository.publish_pair(
        task.id,
        SnapshotDraft(
            id=source_snapshot_id,
            source_file_id=source.id,
            source_role=SourceRole.AUTHORITATIVE,
            file_hash=source.sha256,
            schema_version="canonical-v1",
            mapping_version="third-party-v1",
            raw_rows=({"row_number": 2, "payload": source_entity.raw_payload},),
            entities=(source_entity,),
            summary=IngestionSummary(accepted=1),
        ),
        SnapshotDraft(
            id=target_snapshot_id,
            source_file_id=target.id,
            source_role=SourceRole.TARGET,
            file_hash=target.sha256,
            schema_version="canonical-v1",
            mapping_version="mofa-v1",
            raw_rows=({"row_number": 2, "payload": target_entity.raw_payload},),
            entities=(target_entity,),
            summary=IngestionSummary(accepted=1),
        ),
    )
    await session.flush()

    assert {snapshot.state for snapshot in snapshots} == {"published"}
    assert await session.scalar(select(func.count()).select_from(RawSnapshotRow)) == 2
    assert await session.scalar(select(func.count()).select_from(CanonicalEntityRecord)) == 2


@pytest.mark.asyncio
async def test_invalid_pair_does_not_publish_one_sided_snapshot(session) -> None:
    task, source, target = await create_task_and_files(session)
    repository = SnapshotRepository(session)
    valid = SnapshotDraft.empty(source.id, SourceRole.AUTHORITATIVE, source.sha256)
    invalid = SnapshotDraft.empty(target.id, SourceRole.TARGET, target.sha256).with_entities(None)

    with pytest.raises(ValueError, match="validated entities"):
        await repository.publish_pair(task.id, valid, invalid)

    assert await session.scalar(select(func.count()).select_from(Snapshot)) == 0


@pytest.mark.asyncio
async def test_published_snapshot_is_immutable(session) -> None:
    task, source, target = await create_task_and_files(session)
    repository = SnapshotRepository(session)
    source_draft = SnapshotDraft.empty(source.id, SourceRole.AUTHORITATIVE, source.sha256)
    target_draft = SnapshotDraft.empty(target.id, SourceRole.TARGET, target.sha256)
    snapshots = await repository.publish_pair(task.id, source_draft, target_draft)
    await session.flush()

    snapshots[0].mapping_version = "changed"
    with pytest.raises(ValueError, match="immutable"):
        await session.flush()

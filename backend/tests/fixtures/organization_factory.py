from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.files import FileRepository
from app.repositories.snapshots import SnapshotDraft, SnapshotRepository
from app.repositories.tasks import TaskRepository
from app.schemas.canonical_entities import (
    ClassEntity,
    Membership,
    OrganizationUnit,
    SourceRole,
    Student,
    Teacher,
)
from app.schemas.ingestion import IngestionSummary, SnapshotMode, SnapshotScope
from app.schemas.matching import SnapshotPair


async def create_hierarchy_pair(session: AsyncSession) -> SnapshotPair:
    tasks = TaskRepository(session)
    files = FileRepository(session)
    task = await tasks.create(
        scope=SnapshotScope(tenant_id="school-1", scope_id="all", mode=SnapshotMode.FULL),
        idempotency_key=f"resolution-{uuid4()}",
        request_hash="a" * 64,
    )
    source_file = await files.create(
        source_role=SourceRole.AUTHORITATIVE,
        original_name="source.csv",
        storage_name=f"{uuid4()}.csv",
        storage_path=Path(f"/tmp/{uuid4()}.csv"),
        sha256="b" * 64,
        size_bytes=100,
    )
    target_file = await files.create(
        source_role=SourceRole.TARGET,
        original_name="target.csv",
        storage_name=f"{uuid4()}.csv",
        storage_path=Path(f"/tmp/{uuid4()}.csv"),
        sha256="c" * 64,
        size_bytes=100,
    )
    await files.bind_to_task(source_file.id, task.id)
    await files.bind_to_task(target_file.id, task.id)
    await session.flush()

    source_snapshot_id = uuid4()
    target_snapshot_id = uuid4()
    source_entities = (
        OrganizationUnit(
            tenant_id="school-1",
            snapshot_id=source_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            source_id="d-a",
            raw_row_number=1,
            raw_payload={"id": "d-a", "name": "教务处"},
            name="教务处",
            code="DEPT-1",
        ),
        OrganizationUnit(
            tenant_id="school-1",
            snapshot_id=source_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            source_id="d-child",
            raw_row_number=2,
            raw_payload={"id": "d-child", "name": "高一年级组"},
            name="高一年级组",
            parent_source_id="d-a",
        ),
        Teacher(
            tenant_id="school-1",
            snapshot_id=source_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            source_id="t-a",
            raw_row_number=3,
            raw_payload={"id": "t-a", "name": "张三"},
            name="张三",
            department_source_id="d-a",
        ),
        ClassEntity(
            tenant_id="school-1",
            snapshot_id=source_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            source_id="c-a",
            raw_row_number=4,
            raw_payload={"id": "c-a", "name": "高一(1)班"},
            name="高一(1)班",
            grade="高一",
            school_year="2024",
            parent_source_id="d-a",
        ),
        Student(
            tenant_id="school-1",
            snapshot_id=source_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            source_id="s-a",
            raw_row_number=5,
            raw_payload={"id": "s-a", "name": "王小明"},
            name="王小明",
            student_number="STU-1",
            class_source_id="c-a",
        ),
        Membership(
            tenant_id="school-1",
            snapshot_id=source_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            source_id="m-a",
            raw_row_number=6,
            raw_payload={"id": "m-a"},
            member_source_id="s-a",
            container_source_id="c-a",
            role="student",
        ),
    )
    target_entities = (
        OrganizationUnit(
            tenant_id="school-1",
            snapshot_id=target_snapshot_id,
            source_role=SourceRole.TARGET,
            source_id="sw-d1",
            raw_row_number=1,
            raw_payload={"id": "sw-d1", "name": "教务处"},
            name="教务处",
            code="DEPT-1",
        ),
        OrganizationUnit(
            tenant_id="school-1",
            snapshot_id=target_snapshot_id,
            source_role=SourceRole.TARGET,
            source_id="sw-d-child",
            raw_row_number=2,
            raw_payload={"id": "sw-d-child", "name": "高一年级组"},
            name="高一年级组",
            parent_source_id="sw-d1",
        ),
        Teacher(
            tenant_id="school-1",
            snapshot_id=target_snapshot_id,
            source_role=SourceRole.TARGET,
            source_id="sw-t1",
            raw_row_number=3,
            raw_payload={"id": "sw-t1", "name": "张三"},
            name="张三",
            department_source_id="sw-d1",
        ),
        ClassEntity(
            tenant_id="school-1",
            snapshot_id=target_snapshot_id,
            source_role=SourceRole.TARGET,
            source_id="sw-c1",
            raw_row_number=4,
            raw_payload={"id": "sw-c1", "name": "2024级1班"},
            name="2024级1班",
            grade="高一",
            school_year="2024学年",
            parent_source_id="sw-d1",
        ),
        Student(
            tenant_id="school-1",
            snapshot_id=target_snapshot_id,
            source_role=SourceRole.TARGET,
            source_id="sw-s1",
            raw_row_number=5,
            raw_payload={"id": "sw-s1", "name": "王小明"},
            name="王小明",
            student_number="STU-1",
            class_source_id="sw-c1",
        ),
        Membership(
            tenant_id="school-1",
            snapshot_id=target_snapshot_id,
            source_role=SourceRole.TARGET,
            source_id="sw-m1",
            raw_row_number=6,
            raw_payload={"id": "sw-m1"},
            member_source_id="sw-s1",
            container_source_id="sw-c1",
            role="student",
        ),
    )
    repository = SnapshotRepository(session)
    await repository.publish_pair(
        task.id,
        _draft(source_file.id, source_file.sha256, SourceRole.AUTHORITATIVE, source_entities),
        _draft(target_file.id, target_file.sha256, SourceRole.TARGET, target_entities),
    )
    await session.flush()
    return SnapshotPair(
        task_id=task.id,
        tenant_id="school-1",
        source_snapshot_id=source_snapshot_id,
        target_snapshot_id=target_snapshot_id,
    )


def _draft(source_file_id, file_hash, role, entities):
    return SnapshotDraft(
        id=entities[0].snapshot_id,
        source_file_id=source_file_id,
        source_role=role,
        file_hash=file_hash,
        schema_version="canonical-v1",
        mapping_version="test-v1",
        raw_rows=tuple(
            {"row_number": entity.raw_row_number, "payload": entity.raw_payload}
            for entity in entities
        ),
        entities=entities,
        summary=IngestionSummary(accepted=len(entities)),
    )

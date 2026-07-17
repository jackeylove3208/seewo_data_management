from dataclasses import dataclass, replace
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.snapshots import (
    CanonicalEntityRecord,
    IngestionIssueRecord,
    RawSnapshotRow,
    Snapshot,
)
from app.schemas.canonical_entities import CanonicalEntity, SourceRole
from app.schemas.ingestion import IngestionIssue, IngestionSummary
from app.snapshots.hashing import hash_canonical_entities


@dataclass(frozen=True)
class SnapshotDraft:
    id: UUID
    source_file_id: UUID
    source_role: SourceRole
    file_hash: str
    schema_version: str
    mapping_version: str
    raw_rows: tuple[dict[str, Any], ...]
    entities: tuple[CanonicalEntity, ...] | None
    summary: IngestionSummary
    warnings: tuple[IngestionIssue, ...] = ()
    quarantined: tuple[IngestionIssue, ...] = ()
    quarantine_path: str | None = None

    @classmethod
    def empty(
        cls,
        source_file_id: UUID,
        source_role: SourceRole,
        file_hash: str,
    ) -> "SnapshotDraft":
        return cls(
            id=uuid4(),
            source_file_id=source_file_id,
            source_role=source_role,
            file_hash=file_hash,
            schema_version="canonical-v1",
            mapping_version="mapping-v1",
            raw_rows=(),
            entities=(),
            summary=IngestionSummary(),
        )

    def with_entities(
        self,
        entities: tuple[CanonicalEntity, ...] | None,
    ) -> "SnapshotDraft":
        return replace(self, entities=entities)


class SnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def publish_pair(
        self,
        task_id: UUID,
        source: SnapshotDraft,
        target: SnapshotDraft,
    ) -> tuple[Snapshot, Snapshot]:
        if source.entities is None or target.entities is None:
            raise ValueError("both snapshot drafts require validated entities")
        if source.source_role is not SourceRole.AUTHORITATIVE:
            raise ValueError("source draft must be authoritative")
        if target.source_role is not SourceRole.TARGET:
            raise ValueError("target draft must be target")
        snapshots = (
            self._snapshot_record(task_id, source),
            self._snapshot_record(task_id, target),
        )
        self.session.add_all(snapshots)
        await self.session.flush()
        self._insert_snapshot_contents(source)
        self._insert_snapshot_contents(target)
        return snapshots

    @staticmethod
    def _snapshot_record(task_id: UUID, draft: SnapshotDraft) -> Snapshot:
        assert draft.entities is not None
        if any(entity.snapshot_id != draft.id for entity in draft.entities):
            raise ValueError("canonical entity snapshot provenance does not match draft")
        return Snapshot(
            id=draft.id,
            task_id=task_id,
            source_file_id=draft.source_file_id,
            source_role=draft.source_role.value,
            schema_version=draft.schema_version,
            mapping_version=draft.mapping_version,
            file_hash=draft.file_hash,
            content_hash=hash_canonical_entities(draft.entities),
            state="published",
            summary=draft.summary.model_dump(mode="json"),
            quarantine_path=draft.quarantine_path,
        )

    def _insert_snapshot_contents(self, draft: SnapshotDraft) -> None:
        assert draft.entities is not None
        self.session.add_all(
            RawSnapshotRow(
                snapshot_id=draft.id,
                row_number=int(raw_row["row_number"]),
                payload=raw_row["payload"],
            )
            for raw_row in draft.raw_rows
        )
        self.session.add_all(
            CanonicalEntityRecord(
                snapshot_id=draft.id,
                entity_type=entity.entity_type.value,
                source_id=entity.source_id,
                raw_row_number=entity.raw_row_number,
                canonical_payload=entity.model_dump(mode="json"),
                raw_payload=entity.raw_payload,
            )
            for entity in draft.entities
        )
        self.session.add_all(
            IngestionIssueRecord(
                snapshot_id=draft.id,
                severity=severity,
                row_number=issue.row_number,
                code=issue.code,
                field=issue.field,
                message=issue.message,
                original_value=issue.original_value,
            )
            for severity, issues in (
                ("warning", draft.warnings),
                ("quarantined", draft.quarantined),
            )
            for issue in issues
        )

    async def list_published(self, task_id: UUID) -> tuple[Snapshot, ...]:
        records = await self.session.scalars(
            select(Snapshot)
            .where(Snapshot.task_id == task_id, Snapshot.state == "published")
            .order_by(Snapshot.source_role)
        )
        return tuple(records)

    async def get_for_task_role(
        self,
        task_id: UUID,
        source_role: SourceRole,
    ) -> Snapshot | None:
        return cast(
            Snapshot | None,
            await self.session.scalar(
                select(Snapshot).where(
                    Snapshot.task_id == task_id,
                    Snapshot.source_role == source_role.value,
                )
            ),
        )

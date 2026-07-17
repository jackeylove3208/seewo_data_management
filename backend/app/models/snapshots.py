from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SourceFile(Base, TimestampMixin):
    __tablename__ = "source_files"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reconciliation_tasks.id"),
        nullable=True,
        index=True,
    )
    source_role: Mapped[str] = mapped_column(String(32))
    original_name: Mapped[str] = mapped_column(String(255))
    storage_name: Mapped[str] = mapped_column(String(80), unique=True)
    storage_path: Mapped[str] = mapped_column(String(1024), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    detected_encoding: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("task_id", "source_role", name="uq_task_source_role"),
        CheckConstraint("size_bytes > 0", name="ck_source_file_non_empty"),
    )


class Snapshot(Base, TimestampMixin):
    __tablename__ = "snapshots"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_tasks.id"),
        index=True,
    )
    source_file_id: Mapped[UUID] = mapped_column(ForeignKey("source_files.id"))
    source_role: Mapped[str] = mapped_column(String(32))
    schema_version: Mapped[str] = mapped_column(String(64))
    mapping_version: Mapped[str] = mapped_column(String(64))
    file_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="published")
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    quarantine_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (UniqueConstraint("task_id", "source_role", name="uq_snapshot_task_role"),)


class RawSnapshotRow(Base):
    __tablename__ = "raw_snapshot_rows"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    row_number: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)

    __table_args__ = (UniqueConstraint("snapshot_id", "row_number", name="uq_raw_snapshot_row"),)


class CanonicalEntityRecord(Base):
    __tablename__ = "canonical_entities"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(255), index=True)
    raw_row_number: Mapped[int]
    canonical_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "entity_type",
            "raw_row_number",
            name="uq_canonical_snapshot_type_row",
        ),
    )


class IngestionIssueRecord(Base):
    __tablename__ = "ingestion_issues"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    severity: Mapped[str] = mapped_column(String(32))
    row_number: Mapped[int | None] = mapped_column(nullable=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str] = mapped_column(String(2000))
    original_value: Mapped[str | None] = mapped_column(String(2000), nullable=True)


def _reject_mutation(_mapper: object, _connection: object, target: object) -> None:
    raise ValueError(f"{type(target).__name__} is immutable")


for immutable_model in (Snapshot, RawSnapshotRow, CanonicalEntityRecord, IngestionIssueRecord):
    event.listen(immutable_model, "before_update", _reject_mutation)
    event.listen(immutable_model, "before_delete", _reject_mutation)

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class EntityMapping(Base, TimestampMixin):
    __tablename__ = "entity_mappings"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_tasks.id"),
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    source_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    target_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    source_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("canonical_entities.id"),
        index=True,
    )
    source_key: Mapped[str] = mapped_column(String(512), index=True)
    target_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("canonical_entities.id"),
        nullable=True,
        index=True,
    )
    target_key: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    rule_version: Mapped[str] = mapped_column(String(64))
    confirmed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    supersedes_mapping_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("entity_mappings.id"), nullable=True, index=True
    )

    __table_args__ = (
        Index(
            "uq_active_confirmed_source_mapping",
            "tenant_id",
            "source_key",
            unique=True,
            sqlite_where=text("confirmed_by IS NOT NULL AND revoked_at IS NULL"),
            postgresql_where=text("confirmed_by IS NOT NULL AND revoked_at IS NULL"),
        ),
        Index(
            "uq_active_confirmed_target_mapping",
            "tenant_id",
            "target_key",
            unique=True,
            sqlite_where=text(
                "confirmed_by IS NOT NULL AND revoked_at IS NULL AND target_key IS NOT NULL"
            ),
            postgresql_where=text(
                "confirmed_by IS NOT NULL AND revoked_at IS NULL AND target_key IS NOT NULL"
            ),
        ),
    )


class SnapshotEntityEmbedding(Base, TimestampMixin):
    __tablename__ = "snapshot_entity_embeddings"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("canonical_entities.id"),
        index=True,
    )
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    source_role: Mapped[str] = mapped_column(String(32), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    campus_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    grade: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_id: Mapped[str] = mapped_column(String(255))
    normalized_values: Mapped[dict[str, str | None]] = mapped_column(JSON)
    parent_mapping_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    block_key: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    provider: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(255))
    dimensions: Mapped[int]
    representation_version: Mapped[str] = mapped_column(String(64))
    representation: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536).with_variant(JSON(), "sqlite"))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "snapshot_id",
            "source_role",
            "entity_type",
            "entity_id",
            "provider",
            "model",
            "representation_version",
            name="uq_snapshot_embedding_version",
        ),
        Index(
            "ix_target_embedding_partition",
            "snapshot_id",
            "tenant_id",
            "entity_type",
            "campus_id",
            "grade",
            "parent_mapping_id",
        ),
        Index(
            "ix_snapshot_embedding_partition",
            "tenant_id",
            "snapshot_id",
            "source_role",
            "entity_type",
            "campus_id",
            "grade",
            "parent_mapping_id",
        ),
    )


# Transitional import compatibility while callers move to the role-aware name.
TargetEntityEmbedding = SnapshotEntityEmbedding

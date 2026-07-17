from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ImmutableDifferenceError(ValueError):
    pass


class DifferenceRecord(Base, TimestampMixin):
    __tablename__ = "difference_items"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_tasks.id"),
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    source_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    target_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    mapping_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("entity_mappings.id"),
        nullable=True,
        index=True,
    )
    source_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("canonical_entities.id"),
        nullable=True,
        index=True,
    )
    target_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("canonical_entities.id"),
        nullable=True,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    difference_type: Mapped[str] = mapped_column(String(32), index=True)
    resolution_status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    analysis_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    risk: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    proposed_action: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    comparison_rule_version: Mapped[str] = mapped_column(String(64))
    evidence_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(default=1)

    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "source_snapshot_id",
            "target_snapshot_id",
            "entity_type",
            "evidence_hash",
            name="uq_difference_evidence",
        ),
        Index(
            "ix_difference_task_filters",
            "task_id",
            "entity_type",
            "difference_type",
            "resolution_status",
            "created_at",
            "id",
        ),
    )


def _reject_mutation(_mapper: object, _connection: object, target: object) -> None:
    raise ImmutableDifferenceError(f"{type(target).__name__} is immutable")


event.listen(DifferenceRecord, "before_update", _reject_mutation)
event.listen(DifferenceRecord, "before_delete", _reject_mutation)

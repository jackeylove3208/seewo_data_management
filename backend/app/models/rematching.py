from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class EntityRematchJobRecord(Base, TimestampMixin):
    __tablename__ = "entity_rematch_jobs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    requested_by: Mapped[str] = mapped_column(String(128))
    source_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    target_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    indexed: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    ai_recovered: Mapped[int] = mapped_column(Integer, default=0)
    no_match: Mapped[int] = mapped_column(Integer, default=0)
    manual_review: Mapped[int] = mapped_column(Integer, default=0)
    conflict: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_cursor: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "task_id",
            "idempotency_key",
            name="uq_entity_rematch_job_idempotency",
        ),
        Index("ix_entity_rematch_jobs_task_status", "tenant_id", "task_id", "status"),
    )


class EntityRematchWorkItemRecord(Base, TimestampMixin):
    __tablename__ = "entity_rematch_work_items"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("entity_rematch_jobs.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    focal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("canonical_entities.id"), index=True)
    focal_role: Mapped[str] = mapped_column(String(32))
    candidate_set_hash: Mapped[str] = mapped_column(String(64), index=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reused_from_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("entity_rematch_work_items.id"), nullable=True, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "focal_role",
            "focal_entity_id",
            name="uq_entity_rematch_work_item_focal",
        ),
        Index(
            "ix_entity_rematch_work_items_claim",
            "tenant_id",
            "job_id",
            "status",
            "available_at",
            "lease_expires_at",
        ),
        Index(
            "ix_entity_rematch_outcome_reuse",
            "tenant_id",
            "entity_type",
            "focal_role",
            "focal_entity_id",
            "candidate_set_hash",
            "policy_version",
            "outcome_status",
        ),
    )


class EntityRematchCandidateEdgeRecord(Base, TimestampMixin):
    __tablename__ = "entity_rematch_candidate_edges"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("entity_rematch_jobs.id"), index=True)
    work_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("entity_rematch_work_items.id"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    focal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("canonical_entities.id"))
    focal_role: Mapped[str] = mapped_column(String(32))
    candidate_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("canonical_entities.id"), index=True
    )
    candidate_role: Mapped[str] = mapped_column(String(32))
    rank: Mapped[int] = mapped_column(Integer)
    vector_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    lexical_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    representation_version: Mapped[str] = mapped_column(String(64))
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict
    )

    __table_args__ = (
        UniqueConstraint(
            "work_item_id",
            "candidate_role",
            "candidate_entity_id",
            name="uq_entity_rematch_candidate_edge",
        ),
        Index(
            "ix_entity_rematch_candidate_rank",
            "tenant_id",
            "work_item_id",
            "rank",
        ),
    )

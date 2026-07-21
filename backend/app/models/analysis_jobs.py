from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
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


class AnalysisJobRecord(Base, TimestampMixin):
    __tablename__ = "analysis_jobs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    requested_by: Mapped[str] = mapped_column(String(128), index=True)
    analysis_version: Mapped[str] = mapped_column(String(64), default="analysis-v3")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    manual_required: Mapped[int] = mapped_column(Integer, default=0)
    needs_information: Mapped[int] = mapped_column(Integer, default=0)
    manual_only: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    proposal_ready: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_cursor: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "task_id",
            "idempotency_key",
            name="uq_analysis_job_idempotency",
        ),
        Index("ix_analysis_jobs_task_status", "task_id", "status"),
    )


class AnalysisWorkItemRecord(Base, TimestampMixin):
    __tablename__ = "analysis_work_items"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_jobs.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    difference_id: Mapped[UUID] = mapped_column(ForeignKey("difference_items.id"), index=True)
    difference_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("analysis_results.id"), nullable=True, index=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolution_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fallback: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "difference_id",
            "difference_version",
            name="uq_analysis_work_item_difference",
        ),
        Index(
            "ix_analysis_work_items_claim",
            "job_id",
            "status",
            "available_at",
            "lease_expires_at",
        ),
    )

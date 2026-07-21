from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class MatchingQualityRecord(Base, TimestampMixin):
    __tablename__ = "matching_quality_results"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    mapping_versions: Mapped[list[str]] = mapped_column(JSON)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_matching_quality_task_latest", "tenant_id", "task_id", "evaluated_at"),
    )

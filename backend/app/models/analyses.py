from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ImmutableAnalysisError(ValueError):
    pass


class AnalysisRecord(Base):
    __tablename__ = "analysis_results"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    difference_id: Mapped[UUID] = mapped_column(ForeignKey("difference_items.id"), index=True)
    difference_version: Mapped[int] = mapped_column(Integer)
    analysis_version: Mapped[str] = mapped_column(String(64), default="analysis-v1")
    status: Mapped[str] = mapped_column(String(32), index=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(255))
    skill_name: Mapped[str] = mapped_column(String(128))
    skill_version: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    tool_trace_ids: Mapped[list[str]] = mapped_column(JSON)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "difference_id",
            "difference_version",
            "analysis_version",
            name="uq_analysis_difference_version",
        ),
    )


def _reject_mutation(_mapper: object, _connection: object, target: object) -> None:
    raise ImmutableAnalysisError(f"{type(target).__name__} is immutable")


event.listen(AnalysisRecord, "before_update", _reject_mutation)
event.listen(AnalysisRecord, "before_delete", _reject_mutation)

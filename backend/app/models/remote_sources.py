from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class RemoteSourceRecord(Base, TimestampMixin):
    __tablename__ = "remote_sources"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    created_by: Mapped[str] = mapped_column(String(255), index=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reconciliation_tasks.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_file_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_files.id", ondelete="RESTRICT"),
        nullable=True,
    )
    original_url: Mapped[str] = mapped_column(Text)
    display_origin: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(32), default="registered")
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    safe_problem_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("uq_remote_sources_task_id", "task_id", unique=True),
        Index("uq_remote_sources_source_file_id", "source_file_id", unique=True),
    )

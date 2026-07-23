from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ReconciliationTask(Base, TimestampMixin):
    __tablename__ = "reconciliation_tasks"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    scope_id: Mapped[str] = mapped_column(String(128))
    snapshot_mode: Mapped[str] = mapped_column(String(32))
    entity_types: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="ingestion")
    workflow_version: Mapped[str] = mapped_column(
        String(32), default="legacy-v1", server_default="legacy-v1", index=True
    )
    task_kind: Mapped[str] = mapped_column(
        String(32), default="sync", server_default="sync", index=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_intent: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    parent_task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reconciliation_tasks.id"), nullable=True, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

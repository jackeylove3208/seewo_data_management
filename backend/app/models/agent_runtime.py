from datetime import UTC, datetime
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
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.type_api import TypeEngine

from app.models.base import Base, TimestampMixin


def _json_type() -> TypeEngine[Any]:
    return JSON().with_variant(JSONB(), "postgresql")


class AgentConversationRecord(Base, TimestampMixin):
    __tablename__ = "agent_conversations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    created_by: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    context: Mapped[dict[str, Any]] = mapped_column(_json_type(), default=dict)
    reset_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "created_by",
            "reset_idempotency_key",
            name="uq_agent_conversation_reset_key",
        ),
        Index(
            "uq_agent_conversations_active_operator",
            "tenant_id",
            "created_by",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )


class AgentConversationMessageRecord(Base, TimestampMixin):
    __tablename__ = "agent_conversation_messages"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32), default="normal")
    text: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_agent_conversation_message_sequence",
        ),
    )


class AgentRunRecord(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_tasks.id", ondelete="CASCADE"), unique=True, index=True
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    workflow_version: Mapped[str] = mapped_column(String(32), default="new-agent-v1")
    ingestion_contract_version: Mapped[str] = mapped_column(
        String(64),
        default="model-mediated-ingestion-v1",
        server_default="model-mediated-ingestion-v1",
    )
    execution_contract_version: Mapped[str] = mapped_column(
        String(64),
        default="model-mediated-execution-v1",
        server_default="model-mediated-execution-v1",
    )
    phase: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    progress_completed: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    skill_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    skill_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class AgentTaskEventRecord(Base, TimestampMixin):
    __tablename__ = "agent_task_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(_json_type(), default=dict)

    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_agent_task_event_sequence"),)


class AgentCheckpointRecord(Base, TimestampMixin):
    __tablename__ = "agent_checkpoints"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    phase: Mapped[str] = mapped_column(String(64), index=True)
    checkpoint_key: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(_json_type(), default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("run_id", "phase", "checkpoint_key", name="uq_agent_checkpoint_key"),
    )


class AgentFailureRecord(Base, TimestampMixin):
    __tablename__ = "agent_failures"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    phase: Mapped[str] = mapped_column(String(64), index=True)
    code: Mapped[str] = mapped_column(String(128), index=True)
    safe_message: Mapped[str] = mapped_column(String(512))
    gateway_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict[str, Any]] = mapped_column(_json_type(), default=dict)


class AgentDatabaseSchemaMappingRecord(Base, TimestampMixin):
    __tablename__ = "agent_database_schema_mappings"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    authoritative_connector_id: Mapped[str] = mapped_column(String(128))
    target_connector_id: Mapped[str] = mapped_column(String(128))
    authoritative_schema_fingerprint: Mapped[str] = mapped_column(String(71))
    target_schema_fingerprint: Mapped[str] = mapped_column(String(71))
    ingestion_contract_version: Mapped[str] = mapped_column(String(64))
    skill_name: Mapped[str] = mapped_column(String(128))
    skill_version: Mapped[str] = mapped_column(String(64))
    mapping: Mapped[dict[str, Any]] = mapped_column(_json_type())
    content_hash: Mapped[str] = mapped_column(String(71))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "authoritative_connector_id",
            "target_connector_id",
            "authoritative_schema_fingerprint",
            "target_schema_fingerprint",
            "ingestion_contract_version",
            "skill_name",
            "skill_version",
            name="uq_agent_database_schema_mapping_cache",
        ),
    )


class SchoolTaskLockRecord(Base):
    __tablename__ = "school_task_locks"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    owner_task_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_tasks.id", ondelete="CASCADE"), index=True
    )
    owner_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index(
            "uq_school_task_locks_active_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active = 1"),
        ),
    )

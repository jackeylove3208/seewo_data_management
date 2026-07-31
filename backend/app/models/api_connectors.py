from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _json() -> JSON:
    return JSON().with_variant(JSONB(), "postgresql")


class ApiConnectionRecord(Base, TimestampMixin):
    __tablename__ = "api_connections"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    provider_id: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    scope: Mapped[str] = mapped_column(
        String(32),
        default="persistent",
        server_default="persistent",
        index=True,
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reconciliation_tasks.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    consumed_task_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    public_configuration: Mapped[dict[str, Any]] = mapped_column(_json(), default=dict)
    secret_ref: Mapped[str] = mapped_column(String(128))
    manifest_version: Mapped[str] = mapped_column(String(64))
    adapter_version: Mapped[str] = mapped_column(String(64))
    capabilities: Mapped[dict[str, Any]] = mapped_column(_json(), default=dict)
    visibility_summary: Mapped[dict[str, Any]] = mapped_column(_json(), default=dict)
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_safe_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    credentials_revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    disabled_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), index=True)
    updated_by: Mapped[str] = mapped_column(String(255), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "uq_api_connections_tenant_display_name",
            "tenant_id",
            "display_name",
            unique=True,
        ),
        Index(
            "uq_api_connections_unbound_dingtalk_conversation",
            "tenant_id",
            "conversation_id",
            "provider_id",
            unique=True,
            postgresql_where=text(
                "provider_id = 'dingtalk' AND scope = 'task_ephemeral' "
                "AND task_id IS NULL AND credentials_revoked_at IS NULL"
            ),
            sqlite_where=text(
                "provider_id = 'dingtalk' AND scope = 'task_ephemeral' "
                "AND task_id IS NULL AND credentials_revoked_at IS NULL"
            ),
        ),
        CheckConstraint(
            "state IN ('pending', 'active', 'invalid', 'disabled')",
            name="ck_api_connection_state",
        ),
        CheckConstraint(
            "scope IN ('persistent', 'task_ephemeral')",
            name="ck_api_connection_scope",
        ),
        CheckConstraint(
            "(scope = 'persistent' AND conversation_id IS NULL AND task_id IS NULL) "
            "OR (scope = 'task_ephemeral' AND "
            "(conversation_id IS NOT NULL OR credentials_revoked_at IS NOT NULL))",
            name="ck_api_connection_scope_binding",
        ),
        CheckConstraint(
            "credentials_revoked_at IS NULL OR state = 'disabled'",
            name="ck_api_connection_revoked_state",
        ),
    )


class ApiConnectionSecretRecord(Base, TimestampMixin):
    __tablename__ = "api_connection_secrets"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[str] = mapped_column(String(64))


class ApiConfigurationSessionRecord(Base, TimestampMixin):
    __tablename__ = "api_configuration_sessions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    provider_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    connection_scope: Mapped[str] = mapped_column(
        String(32),
        default="persistent",
        server_default="persistent",
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiAuthoritySourceRecord(Base, TimestampMixin):
    __tablename__ = "api_authority_sources"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_tasks.id", ondelete="RESTRICT"),
        index=True,
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_connections.id", ondelete="RESTRICT"),
        index=True,
    )
    frozen_public_configuration: Mapped[dict[str, Any]] = mapped_column(
        _json(),
        default=dict,
    )
    frozen_secret_ref: Mapped[str] = mapped_column(String(128))
    selected_entities: Mapped[list[str]] = mapped_column(_json(), default=list)
    selection_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="registered", index=True)
    source_file_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_files.id", ondelete="RESTRICT"),
        nullable=True,
    )
    snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manifest_version: Mapped[str] = mapped_column(String(64))
    adapter_version: Mapped[str] = mapped_column(String(64))
    projection_version: Mapped[str] = mapped_column(String(64))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    safe_problem_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("uq_api_authority_sources_task_id", "task_id", unique=True),
        Index(
            "uq_api_authority_sources_source_file_id",
            "source_file_id",
            unique=True,
        ),
        Index(
            "uq_api_authority_sources_snapshot_id",
            "snapshot_id",
            unique=True,
        ),
        CheckConstraint(
            "state IN ('registered', 'materializing', 'ready', 'failed')",
            name="ck_api_authority_source_state",
        ),
        CheckConstraint(
            "record_count IS NULL OR record_count >= 0",
            name="ck_api_authority_source_record_count",
        ),
        CheckConstraint(
            "page_count IS NULL OR page_count >= 0",
            name="ck_api_authority_source_page_count",
        ),
    )


class AgentSourceBindingRecord(Base, TimestampMixin):
    __tablename__ = "agent_source_bindings"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_tasks.id", ondelete="RESTRICT"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32))
    connector_kind: Mapped[str] = mapped_column(String(32))
    configuration_id: Mapped[str] = mapped_column(String(512))
    snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    frozen_public_configuration: Mapped[dict[str, Any]] = mapped_column(
        _json(),
        default=dict,
    )
    credential_reference: Mapped[str] = mapped_column(String(512))
    mapping_checkpoint_key: Mapped[str] = mapped_column(String(255))
    normalization_checkpoint_key: Mapped[str] = mapped_column(String(255))

    __table_args__ = (
        Index(
            "uq_agent_source_bindings_task_role",
            "task_id",
            "role",
            unique=True,
        ),
        CheckConstraint(
            "role IN ('authoritative', 'target')",
            name="ck_agent_source_binding_role",
        ),
        CheckConstraint(
            "connector_kind IN ('api', 'database', 'csv')",
            name="ck_agent_source_binding_connector_kind",
        ),
    )


class AgentExternalIdentityBindingRecord(Base, TimestampMixin):
    __tablename__ = "agent_external_identity_bindings"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    provider_id: Mapped[str] = mapped_column(String(64), index=True)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_connections.id", ondelete="RESTRICT"),
        index=True,
    )
    entity_kind: Mapped[str] = mapped_column(String(32), index=True)
    authority_stable_locator: Mapped[str] = mapped_column(String(512))
    target_connector_id: Mapped[str] = mapped_column(String(128))
    target_stable_locator: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    binding_version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_by: Mapped[str] = mapped_column(String(255), index=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        Index(
            "uq_agent_external_binding_authority",
            "tenant_id",
            "provider_id",
            "connection_id",
            "entity_kind",
            "authority_stable_locator",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_agent_external_binding_target",
            "tenant_id",
            "connection_id",
            "entity_kind",
            "target_connector_id",
            "target_stable_locator",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        CheckConstraint(
            "entity_kind IN ('department', 'student', 'teacher')",
            name="ck_agent_external_binding_entity_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_agent_external_binding_status",
        ),
        CheckConstraint(
            "binding_version >= 1",
            name="ck_agent_external_binding_version",
        ),
    )

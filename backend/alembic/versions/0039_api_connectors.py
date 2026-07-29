"""Add organization API connector records.

Revision ID: 0039_api_connectors
Revises: 0038_expand_storage_name
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0039_api_connectors"
down_revision: str | Sequence[str] | None = "0038_expand_storage_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.is_offline_mode():
        tables = set(sa.inspect(op.get_bind()).get_table_names())
        if "api_connections" in tables:
            return

    op.create_table(
        "api_connection_secrets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_api_connection_secrets_tenant_id",
        "api_connection_secrets",
        ["tenant_id"],
        unique=False,
    )

    op.create_table(
        "api_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("public_configuration", sa.JSON(), nullable=False),
        sa.Column("secret_ref", sa.String(length=128), nullable=False),
        sa.Column("manifest_version", sa.String(length=64), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("visibility_summary", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_safe_error_code", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'active', 'invalid', 'disabled')",
            name="ck_api_connection_state",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns, unique in (
        ("ix_api_connections_tenant_id", ["tenant_id"], False),
        ("ix_api_connections_provider_id", ["provider_id"], False),
        ("ix_api_connections_state", ["state"], False),
        ("ix_api_connections_created_by", ["created_by"], False),
        ("ix_api_connections_updated_by", ["updated_by"], False),
        (
            "uq_api_connections_tenant_display_name",
            ["tenant_id", "display_name"],
            True,
        ),
    ):
        op.create_index(name, "api_connections", columns, unique=unique)

    op.create_table(
        "api_authority_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("selected_entities", sa.JSON(), nullable=False),
        sa.Column("selection_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("source_file_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("manifest_version", sa.String(length=64), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("projection_version", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_problem_code", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('registered', 'materializing', 'ready', 'failed')",
            name="ck_api_authority_source_state",
        ),
        sa.CheckConstraint(
            "record_count IS NULL OR record_count >= 0",
            name="ck_api_authority_source_record_count",
        ),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count >= 0",
            name="ck_api_authority_source_page_count",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["api_connections.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_file_id"],
            ["source_files.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["reconciliation_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns, unique in (
        ("ix_api_authority_sources_tenant_id", ["tenant_id"], False),
        ("ix_api_authority_sources_task_id", ["task_id"], False),
        ("ix_api_authority_sources_connection_id", ["connection_id"], False),
        ("ix_api_authority_sources_state", ["state"], False),
        ("uq_api_authority_sources_task_id", ["task_id"], True),
        ("uq_api_authority_sources_source_file_id", ["source_file_id"], True),
        ("uq_api_authority_sources_snapshot_id", ["snapshot_id"], True),
    ):
        op.create_index(name, "api_authority_sources", columns, unique=unique)

    op.create_table(
        "agent_external_identity_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("entity_kind", sa.String(length=32), nullable=False),
        sa.Column("authority_stable_locator", sa.String(length=512), nullable=False),
        sa.Column("target_connector_id", sa.String(length=128), nullable=False),
        sa.Column("target_stable_locator", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=255), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.String(length=255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity_kind IN ('department', 'student', 'teacher')",
            name="ck_agent_external_binding_entity_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_agent_external_binding_status",
        ),
        sa.CheckConstraint(
            "binding_version >= 1",
            name="ck_agent_external_binding_version",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["api_connections.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_agent_external_identity_bindings_tenant_id", ["tenant_id"]),
        ("ix_agent_external_identity_bindings_provider_id", ["provider_id"]),
        ("ix_agent_external_identity_bindings_connection_id", ["connection_id"]),
        ("ix_agent_external_identity_bindings_entity_kind", ["entity_kind"]),
        ("ix_agent_external_identity_bindings_status", ["status"]),
        ("ix_agent_external_identity_bindings_confirmed_by", ["confirmed_by"]),
    ):
        op.create_index(
            name,
            "agent_external_identity_bindings",
            columns,
            unique=False,
        )
    active = sa.text("status = 'active'")
    op.create_index(
        "uq_agent_external_binding_authority",
        "agent_external_identity_bindings",
        [
            "tenant_id",
            "provider_id",
            "connection_id",
            "entity_kind",
            "authority_stable_locator",
        ],
        unique=True,
        sqlite_where=active,
        postgresql_where=active,
    )
    op.create_index(
        "uq_agent_external_binding_target",
        "agent_external_identity_bindings",
        [
            "tenant_id",
            "connection_id",
            "entity_kind",
            "target_connector_id",
            "target_stable_locator",
        ],
        unique=True,
        sqlite_where=active,
        postgresql_where=active,
    )


def downgrade() -> None:
    if not context.is_offline_mode():
        tables = set(sa.inspect(op.get_bind()).get_table_names())
        if "api_connections" not in tables:
            return
    op.drop_table("agent_external_identity_bindings")
    op.drop_table("api_authority_sources")
    op.drop_table("api_connections")
    op.drop_table("api_connection_secrets")

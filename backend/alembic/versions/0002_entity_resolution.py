"""Create entity mapping history.

Revision ID: 0002_entity_resolution
Revises: 0001_ingestion
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_entity_resolution"
down_revision: str | None = "0001_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("source_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("target_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("source_entity_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(512), nullable=False),
        sa.Column("target_entity_id", sa.Uuid(), nullable=True),
        sa.Column("target_key", sa.String(512), nullable=True),
        sa.Column("method", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.String(255), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(255), nullable=True),
        sa.Column("revocation_reason", sa.String(2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["snapshots.id"]),
        sa.ForeignKeyConstraint(["target_snapshot_id"], ["snapshots.id"]),
        sa.ForeignKeyConstraint(["source_entity_id"], ["canonical_entities.id"]),
        sa.ForeignKeyConstraint(["target_entity_id"], ["canonical_entities.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_mapping_task", ["task_id"]),
        ("ix_mapping_tenant", ["tenant_id"]),
        ("ix_mapping_source_snapshot", ["source_snapshot_id"]),
        ("ix_mapping_target_snapshot", ["target_snapshot_id"]),
        ("ix_mapping_entity_type", ["entity_type"]),
        ("ix_mapping_source_entity", ["source_entity_id"]),
        ("ix_mapping_source_key", ["source_key"]),
        ("ix_mapping_target_entity", ["target_entity_id"]),
        ("ix_mapping_target_key", ["target_key"]),
        ("ix_mapping_status", ["status"]),
    ):
        op.create_index(name, "entity_mappings", columns)
    active_source = "confirmed_by IS NOT NULL AND revoked_at IS NULL"
    active_target = "confirmed_by IS NOT NULL AND revoked_at IS NULL AND target_key IS NOT NULL"
    op.create_index(
        "uq_active_confirmed_source_mapping",
        "entity_mappings",
        ["tenant_id", "source_key"],
        unique=True,
        sqlite_where=sa.text(active_source),
        postgresql_where=sa.text(active_source),
    )
    op.create_index(
        "uq_active_confirmed_target_mapping",
        "entity_mappings",
        ["tenant_id", "target_key"],
        unique=True,
        sqlite_where=sa.text(active_target),
        postgresql_where=sa.text(active_target),
    )


def downgrade() -> None:
    op.drop_table("entity_mappings")

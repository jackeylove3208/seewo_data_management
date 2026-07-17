"""Create data-source ingestion tables.

Revision ID: 0001_ingestion
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_ingestion"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "reconciliation_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("scope_id", sa.String(128), nullable=False),
        sa.Column("snapshot_mode", sa.String(32), nullable=False),
        sa.Column("entity_types", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_task_tenant", "reconciliation_tasks", ["tenant_id"])
    op.create_index("ix_task_status", "reconciliation_tasks", ["status"])
    op.create_table(
        "source_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("source_role", sa.String(32), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("storage_name", sa.String(80), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("detected_encoding", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_source_file_non_empty"),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_name"),
        sa.UniqueConstraint("storage_path"),
        sa.UniqueConstraint("task_id", "source_role", name="uq_task_source_role"),
    )
    op.create_index("ix_source_file_task", "source_files", ["task_id"])
    op.create_index("ix_source_file_hash", "source_files", ["sha256"])
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("source_file_id", sa.Uuid(), nullable=False),
        sa.Column("source_role", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("mapping_version", sa.String(64), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("quarantine_path", sa.String(1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_file_id"], ["source_files.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "source_role", name="uq_snapshot_task_role"),
    )
    op.create_index("ix_snapshot_task", "snapshots", ["task_id"])
    op.create_table(
        "raw_snapshot_rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "row_number", name="uq_raw_snapshot_row"),
    )
    op.create_index("ix_raw_snapshot", "raw_snapshot_rows", ["snapshot_id"])
    op.create_table(
        "canonical_entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("raw_row_number", sa.Integer(), nullable=False),
        sa.Column("canonical_payload", sa.JSON(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "entity_type",
            "raw_row_number",
            name="uq_canonical_snapshot_type_row",
        ),
    )
    op.create_index("ix_canonical_snapshot", "canonical_entities", ["snapshot_id"])
    op.create_index("ix_canonical_type", "canonical_entities", ["entity_type"])
    op.create_index("ix_canonical_source", "canonical_entities", ["source_id"])
    op.create_table(
        "ingestion_issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("field", sa.String(128), nullable=True),
        sa.Column("message", sa.String(2000), nullable=False),
        sa.Column("original_value", sa.String(2000), nullable=True),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_issue_snapshot", "ingestion_issues", ["snapshot_id"])
    op.create_index("ix_issue_code", "ingestion_issues", ["code"])


def downgrade() -> None:
    op.drop_table("ingestion_issues")
    op.drop_table("canonical_entities")
    op.drop_table("raw_snapshot_rows")
    op.drop_table("snapshots")
    op.drop_table("source_files")
    op.drop_table("reconciliation_tasks")

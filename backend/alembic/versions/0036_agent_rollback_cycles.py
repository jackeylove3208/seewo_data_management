"""Limit rollback to once per target data-source sync cycle.

Revision ID: 0036_agent_rollback_cycles
Revises: 0035_db_schema_mapping_cache
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0036_agent_rollback_cycles"
down_revision: str | Sequence[str] | None = "0035_db_schema_mapping_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.is_offline_mode():
        tables = set(sa.inspect(op.get_bind()).get_table_names())
        if "agent_rollback_cycles" in tables:
            return
    op.create_table(
        "agent_rollback_cycles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("data_source_key", sa.String(length=64), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("latest_successful_sync_task_id", sa.Uuid(), nullable=False),
        sa.Column("completed_rollback_task_id", sa.Uuid(), nullable=True),
        sa.Column("completed_rollback_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["latest_successful_sync_task_id"],
            ["reconciliation_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["completed_rollback_task_id"],
            ["reconciliation_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "data_source_key",
            name="uq_agent_rollback_cycle_data_source",
        ),
    )
    op.create_index(
        "ix_agent_rollback_cycles_tenant_id",
        "agent_rollback_cycles",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_rollback_cycles_latest_successful_sync_task_id",
        "agent_rollback_cycles",
        ["latest_successful_sync_task_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_rollback_cycles_completed_rollback_task_id",
        "agent_rollback_cycles",
        ["completed_rollback_task_id"],
        unique=False,
    )


def downgrade() -> None:
    if not context.is_offline_mode():
        tables = set(sa.inspect(op.get_bind()).get_table_names())
        if "agent_rollback_cycles" not in tables:
            return
    op.drop_index(
        "ix_agent_rollback_cycles_completed_rollback_task_id",
        table_name="agent_rollback_cycles",
    )
    op.drop_index(
        "ix_agent_rollback_cycles_latest_successful_sync_task_id",
        table_name="agent_rollback_cycles",
    )
    op.drop_index(
        "ix_agent_rollback_cycles_tenant_id",
        table_name="agent_rollback_cycles",
    )
    op.drop_table("agent_rollback_cycles")

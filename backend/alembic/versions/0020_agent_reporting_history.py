"""Add Agent terminal reports and rollback task lineage.

Revision ID: 0020_agent_reporting_history
Revises: 0019_agent_lease_fencing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import context, op

revision: str = "0020_agent_reporting_history"
down_revision: str | None = "0019_agent_lease_fencing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = None if context.is_offline_mode() else inspect(op.get_bind())
    tables = set() if inspector is None else set(inspector.get_table_names())
    columns = (
        set()
        if inspector is None
        else {column["name"] for column in inspector.get_columns("reconciliation_tasks")}
    )
    if "task_kind" not in columns:
        op.add_column(
            "reconciliation_tasks",
            sa.Column("task_kind", sa.String(length=32), nullable=False, server_default="sync"),
        )
        op.create_index("ix_reconciliation_tasks_task_kind", "reconciliation_tasks", ["task_kind"])
    if "parent_task_id" not in columns:
        op.add_column(
            "reconciliation_tasks",
            sa.Column("parent_task_id", sa.Uuid(), nullable=True),
        )
        op.create_index(
            "ix_reconciliation_tasks_parent_task_id", "reconciliation_tasks", ["parent_task_id"]
        )
        if op.get_bind().dialect.name != "sqlite":
            op.create_foreign_key(
                "fk_reconciliation_tasks_parent_task_id",
                "reconciliation_tasks",
                "reconciliation_tasks",
                ["parent_task_id"],
                ["id"],
            )
    if "agent_reports" not in tables:
        op.create_table(
            "agent_reports",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("task_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("terminal_state", sa.String(length=64), nullable=False),
            sa.Column("facts", sa.JSON(), nullable=False),
            sa.Column("facts_hash", sa.String(length=64), nullable=False),
            sa.Column("content", sa.JSON(), nullable=False),
            sa.Column("rollback_eligible", sa.Boolean(), nullable=False),
            sa.Column("deletion_eligible", sa.Boolean(), nullable=False),
            sa.Column("generated_by", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("task_id"),
        )
        op.create_index("ix_agent_reports_task_id", "agent_reports", ["task_id"])
        op.create_index("ix_agent_reports_tenant_id", "agent_reports", ["tenant_id"])
        op.create_index("ix_agent_reports_kind", "agent_reports", ["kind"])
        op.create_index("ix_agent_reports_terminal_state", "agent_reports", ["terminal_state"])
        op.create_index(
            "ix_agent_reports_rollback_eligible", "agent_reports", ["rollback_eligible"]
        )
        op.create_index(
            "ix_agent_reports_deletion_eligible", "agent_reports", ["deletion_eligible"]
        )


def downgrade() -> None:
    inspector = None if context.is_offline_mode() else inspect(op.get_bind())
    tables = set() if inspector is None else set(inspector.get_table_names())
    if "agent_reports" in tables:
        op.drop_table("agent_reports")
    columns = (
        set()
        if inspector is None
        else {column["name"] for column in inspector.get_columns("reconciliation_tasks")}
    )
    if "parent_task_id" in columns:
        if op.get_bind().dialect.name != "sqlite":
            op.drop_constraint(
                "fk_reconciliation_tasks_parent_task_id",
                "reconciliation_tasks",
                type_="foreignkey",
            )
        op.drop_index("ix_reconciliation_tasks_parent_task_id", table_name="reconciliation_tasks")
        op.drop_column("reconciliation_tasks", "parent_task_id")
    if "task_kind" in columns:
        op.drop_index("ix_reconciliation_tasks_task_kind", table_name="reconciliation_tasks")
        op.drop_column("reconciliation_tasks", "task_kind")

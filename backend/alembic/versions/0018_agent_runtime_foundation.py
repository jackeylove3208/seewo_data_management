"""Add durable Agent runtime foundation.

Revision ID: 0018_agent_runtime_foundation
Revises: 0017_task_delete_target_version
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import context, op

revision: str = "0018_agent_runtime_foundation"
down_revision: str | None = "0017_task_delete_target_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.JSON:
    return sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = None if context.is_offline_mode() else inspect(bind)
    tables = set() if inspector is None else set(inspector.get_table_names())
    task_columns = (
        set()
        if inspector is None
        else {column["name"] for column in inspector.get_columns("reconciliation_tasks")}
    )
    if "workflow_version" not in task_columns:
        op.add_column(
            "reconciliation_tasks",
            sa.Column(
                "workflow_version",
                sa.String(length=32),
                nullable=False,
                server_default="legacy-v1",
            ),
        )
    indexes = (
        set()
        if inspector is None
        else {index["name"] for index in inspector.get_indexes("reconciliation_tasks")}
    )
    if "ix_reconciliation_tasks_workflow_version" not in indexes:
        op.create_index(
            "ix_reconciliation_tasks_workflow_version",
            "reconciliation_tasks",
            ["workflow_version"],
        )

    if "agent_conversations" not in tables:
        op.create_table(
            "agent_conversations",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("created_by", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("context", _json_type(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_agent_conversations_tenant_id", "agent_conversations", ["tenant_id"])
        op.create_index("ix_agent_conversations_created_by", "agent_conversations", ["created_by"])
        op.create_index("ix_agent_conversations_status", "agent_conversations", ["status"])

    if "agent_runs" not in tables:
        op.create_table(
            "agent_runs",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("task_id", sa.Uuid(), nullable=False),
            sa.Column("conversation_id", sa.Uuid(), nullable=True),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("workflow_version", sa.String(length=32), nullable=False),
            sa.Column("phase", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("progress_completed", sa.Integer(), nullable=False),
            sa.Column("progress_total", sa.Integer(), nullable=False),
            sa.Column("skill_name", sa.String(length=128), nullable=True),
            sa.Column("skill_version", sa.String(length=64), nullable=True),
            sa.Column("lease_owner", sa.String(length=128), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["conversation_id"], ["agent_conversations.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["task_id"], ["reconciliation_tasks.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("task_id"),
        )
        for column in (
            "task_id",
            "conversation_id",
            "tenant_id",
            "kind",
            "phase",
            "status",
            "lease_owner",
            "lease_expires_at",
        ):
            op.create_index(f"ix_agent_runs_{column}", "agent_runs", [column])

    if "agent_task_events" not in tables:
        op.create_table(
            "agent_task_events",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("run_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("payload", _json_type(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", "sequence", name="uq_agent_task_event_sequence"),
        )
        for column in ("run_id", "tenant_id", "event_type"):
            op.create_index(f"ix_agent_task_events_{column}", "agent_task_events", [column])

    if "agent_checkpoints" not in tables:
        op.create_table(
            "agent_checkpoints",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("run_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("phase", sa.String(length=64), nullable=False),
            sa.Column("checkpoint_key", sa.String(length=128), nullable=False),
            sa.Column("input_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("payload", _json_type(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "run_id", "phase", "checkpoint_key", name="uq_agent_checkpoint_key"
            ),
        )
        for column in ("run_id", "tenant_id", "phase", "status"):
            op.create_index(f"ix_agent_checkpoints_{column}", "agent_checkpoints", [column])

    if "agent_failures" not in tables:
        op.create_table(
            "agent_failures",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("run_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("phase", sa.String(length=64), nullable=False),
            sa.Column("code", sa.String(length=128), nullable=False),
            sa.Column("safe_message", sa.String(length=512), nullable=False),
            sa.Column("gateway_request_id", sa.String(length=255), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("details", _json_type(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("run_id", "tenant_id", "phase", "code"):
            op.create_index(f"ix_agent_failures_{column}", "agent_failures", [column])

    if "school_task_locks" not in tables:
        op.create_table(
            "school_task_locks",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("owner_task_id", sa.Uuid(), nullable=False),
            sa.Column("owner_run_id", sa.Uuid(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("release_reason", sa.String(length=64), nullable=True),
            sa.ForeignKeyConstraint(["owner_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["owner_task_id"], ["reconciliation_tasks.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("tenant_id", "owner_task_id", "owner_run_id", "active"):
            op.create_index(f"ix_school_task_locks_{column}", "school_task_locks", [column])
        if bind.dialect.name == "postgresql":
            op.create_index(
                "uq_school_task_locks_active_tenant",
                "school_task_locks",
                ["tenant_id"],
                unique=True,
                postgresql_where=sa.text("active"),
            )
        else:
            op.create_index(
                "uq_school_task_locks_active_tenant",
                "school_task_locks",
                ["tenant_id"],
                unique=True,
                sqlite_where=sa.text("active = 1"),
            )


def downgrade() -> None:
    for table in (
        "school_task_locks",
        "agent_failures",
        "agent_checkpoints",
        "agent_task_events",
        "agent_runs",
        "agent_conversations",
    ):
        op.drop_table(table)
    op.drop_index("ix_reconciliation_tasks_workflow_version", table_name="reconciliation_tasks")
    op.drop_column("reconciliation_tasks", "workflow_version")

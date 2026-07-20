"""Persist reconciliation workflow stage attempts.

Revision ID: 0007_workflow_stage_runs
Revises: 0006_analysis_immutability
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import context, op

revision: str = "0007_workflow_stage_runs"
down_revision: str | None = "0006_analysis_immutability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUIRED_COLUMNS = frozenset(
    {
        "id",
        "task_id",
        "stage",
        "attempt",
        "status",
        "processed",
        "total",
        "succeeded",
        "manual_review",
        "failed",
        "error",
        "retryable",
        "started_at",
        "completed_at",
    }
)
_REQUIRED_UNIQUE = {
    "uq_workflow_stage_attempt": ("task_id", "stage", "attempt"),
}
_REQUIRED_INDEXES = {
    "ix_workflow_stage_runs_task_id": ("task_id",),
    "ix_workflow_stage_runs_stage": ("stage",),
    "ix_workflow_stage_runs_status": ("status",),
}


def upgrade() -> None:
    if not context.is_offline_mode() and sa.inspect(op.get_bind()).has_table(
        "workflow_stage_runs"
    ):
        _validate_and_repair_existing_table()
        return
    json_type = JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()
    op.create_table(
        "workflow_stage_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False),
        sa.Column("manual_review", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("error", json_type, nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "stage",
            "attempt",
            name="uq_workflow_stage_attempt",
        ),
    )
    op.create_index("ix_workflow_stage_runs_task_id", "workflow_stage_runs", ["task_id"])
    op.create_index("ix_workflow_stage_runs_stage", "workflow_stage_runs", ["stage"])
    op.create_index("ix_workflow_stage_runs_status", "workflow_stage_runs", ["status"])


def _validate_and_repair_existing_table() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("workflow_stage_runs")}
    missing_columns = sorted(_REQUIRED_COLUMNS - columns)
    if missing_columns:
        raise RuntimeError(
            "workflow_stage_runs schema is incomplete; missing columns: "
            + ", ".join(missing_columns)
        )
    unique_constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("workflow_stage_runs")
    }
    for name, expected_columns in _REQUIRED_UNIQUE.items():
        if unique_constraints.get(name) != expected_columns:
            raise RuntimeError(
                f"workflow_stage_runs is missing required unique constraint {name}"
            )
    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("workflow_stage_runs")
    }
    for name, expected_columns in _REQUIRED_INDEXES.items():
        actual_columns = indexes.get(name)
        if actual_columns is None:
            op.create_index(name, "workflow_stage_runs", list(expected_columns))
        elif actual_columns != expected_columns:
            raise RuntimeError(
                f"workflow_stage_runs index {name} has unexpected columns"
            )


def downgrade() -> None:
    op.drop_table("workflow_stage_runs")

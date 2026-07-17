"""Persist reconciliation workflow stage attempts.

Revision ID: 0007_workflow_stage_runs
Revises: 0006_analysis_immutability
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0007_workflow_stage_runs"
down_revision: str | None = "0006_analysis_immutability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("workflow_stage_runs")

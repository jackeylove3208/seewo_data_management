"""Persist matching quality gate evaluations.

Revision ID: 0014_matching_quality_results
Revises: 0013_entity_rematch_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0014_matching_quality_results"
down_revision: str | None = "0013_entity_rematch_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.is_offline_mode() and sa.inspect(op.get_bind()).has_table(
        "matching_quality_results"
    ):
        return
    op.create_table(
        "matching_quality_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("mapping_versions", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_matching_quality_results_task_id", "matching_quality_results", ["task_id"])
    op.create_index(
        "ix_matching_quality_results_tenant_id", "matching_quality_results", ["tenant_id"]
    )
    op.create_index(
        "ix_matching_quality_task_latest",
        "matching_quality_results",
        ["tenant_id", "task_id", "evaluated_at"],
    )


def downgrade() -> None:
    if not context.is_offline_mode() and not sa.inspect(op.get_bind()).has_table(
        "matching_quality_results"
    ):
        return
    op.drop_index("ix_matching_quality_task_latest", table_name="matching_quality_results")
    op.drop_index("ix_matching_quality_results_tenant_id", table_name="matching_quality_results")
    op.drop_index("ix_matching_quality_results_task_id", table_name="matching_quality_results")
    op.drop_table("matching_quality_results")

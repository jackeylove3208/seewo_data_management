"""Create immutable AI governance analysis results.

Revision ID: 0005_analysis_results
Revises: 0004_differences
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0005_analysis_results"
down_revision: str | None = "0004_differences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()
    op.create_table(
        "analysis_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("difference_id", sa.Uuid(), nullable=False),
        sa.Column("difference_version", sa.Integer(), nullable=False),
        sa.Column("analysis_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("output", json_type, nullable=True),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("skill_name", sa.String(128), nullable=False),
        sa.Column("skill_version", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("tool_trace_ids", sa.JSON(), nullable=False),
        sa.Column("usage", json_type, nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["difference_id"], ["difference_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "difference_id",
            "difference_version",
            "analysis_version",
            name="uq_analysis_difference_version",
        ),
    )
    op.create_index("ix_analysis_difference", "analysis_results", ["difference_id"])
    op.create_index("ix_analysis_status", "analysis_results", ["status"])


def downgrade() -> None:
    op.drop_table("analysis_results")

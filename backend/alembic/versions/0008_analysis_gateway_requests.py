"""Persist safe enterprise gateway request identifiers.

Revision ID: 0008_analysis_gateway_requests
Revises: 0007_workflow_stage_runs
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_analysis_gateway_requests"
down_revision: str | None = "0007_workflow_stage_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_results",
        sa.Column(
            "gateway_request_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("analysis_results", "gateway_request_ids")

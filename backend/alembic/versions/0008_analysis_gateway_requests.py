"""Persist safe enterprise gateway request identifiers.

Revision ID: 0008_analysis_gateway_requests
Revises: 0007_workflow_stage_runs
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0008_analysis_gateway_requests"
down_revision: str | None = "0007_workflow_stage_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.is_offline_mode():
        columns = {
            column["name"]: column
            for column in sa.inspect(op.get_bind()).get_columns("analysis_results")
        }
        existing = columns.get("gateway_request_ids")
        if existing is not None:
            if existing["nullable"] or not _is_empty_list_default(existing["default"]):
                raise RuntimeError(
                    "analysis_results.gateway_request_ids must be NOT NULL "
                    "with an empty-list server default"
                )
            return
    op.add_column(
        "analysis_results",
        sa.Column(
            "gateway_request_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def _is_empty_list_default(value: object | None) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().casefold().replace(" ", "")
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1]
    for suffix in ("::jsonb", "::json"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized == "'[]'"


def downgrade() -> None:
    op.drop_column("analysis_results", "gateway_request_ids")

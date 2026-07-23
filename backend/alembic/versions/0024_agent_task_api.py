"""Persist Agent task title and connector intent.

Revision ID: 0024_agent_task_api
Revises: 0023_merge_agent_governance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "0024_agent_task_api"
down_revision: str | Sequence[str] | None = "0023_merge_agent_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = _columns()
    if "title" not in columns:
        op.add_column(
            "reconciliation_tasks", sa.Column("title", sa.String(255), nullable=True)
        )
    if "agent_intent" not in columns:
        op.add_column(
            "reconciliation_tasks",
            sa.Column(
                "agent_intent",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                nullable=True,
            ),
        )


def _columns() -> set[str]:
    if context.is_offline_mode():
        return set()
    return {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("reconciliation_tasks")
    }


def downgrade() -> None:
    columns = _columns() if not context.is_offline_mode() else {"title", "agent_intent"}
    if "agent_intent" in columns:
        op.drop_column("reconciliation_tasks", "agent_intent")
    if "title" in columns:
        op.drop_column("reconciliation_tasks", "title")

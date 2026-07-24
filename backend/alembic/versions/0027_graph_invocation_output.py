"""Persist validated graph invocation output for crash-safe replay.

Revision ID: 0027_graph_invocation_output
Revises: 0026_graph_multi_skill
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "0027_graph_invocation_output"
down_revision: str | Sequence[str] | None = "0026_graph_multi_skill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    if not context.is_offline_mode():
        inspector = inspect(op.get_bind())
        if "agent_subagent_invocations" not in inspector.get_table_names():
            return
        columns = {
            column["name"]
            for column in inspector.get_columns("agent_subagent_invocations")
        }
        if "output_payload" in columns:
            return
    with op.batch_alter_table("agent_subagent_invocations") as batch:
        batch.add_column(
            sa.Column(
                "output_payload",
                _json_type(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    if not context.is_offline_mode():
        inspector = inspect(op.get_bind())
        if "agent_subagent_invocations" not in inspector.get_table_names():
            return
        columns = {
            column["name"]
            for column in inspector.get_columns("agent_subagent_invocations")
        }
        if "output_payload" not in columns:
            return
    with op.batch_alter_table("agent_subagent_invocations") as batch:
        batch.drop_column("output_payload")

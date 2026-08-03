"""Persist replay-safe Agent tool-call checkpoints.

Revision ID: 0044_resumable_agent_tool_calls
Revises: 0043_superseded_model_batches
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "0044_resumable_agent_tool_calls"
down_revision: str | Sequence[str] | None = "0043_superseded_model_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _column_names() -> set[str]:
    if context.is_offline_mode():
        return set()
    inspector = inspect(op.get_bind())
    if "agent_tool_calls" not in inspector.get_table_names():
        return {"model_turn", "replay_descriptor"}
    return {
        str(column["name"])
        for column in inspector.get_columns("agent_tool_calls")
    }


def upgrade() -> None:
    columns = _column_names()
    with op.batch_alter_table("agent_tool_calls") as batch_op:
        if "model_turn" not in columns:
            batch_op.add_column(sa.Column("model_turn", sa.Integer(), nullable=True))
        if "replay_descriptor" not in columns:
            batch_op.add_column(
                sa.Column("replay_descriptor", _json_type(), nullable=True)
            )


def downgrade() -> None:
    columns = _column_names()
    with op.batch_alter_table("agent_tool_calls") as batch_op:
        if "replay_descriptor" in columns:
            batch_op.drop_column("replay_descriptor")
        if "model_turn" in columns:
            batch_op.drop_column("model_turn")

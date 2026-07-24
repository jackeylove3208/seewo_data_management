"""Persist public Agent conversation messages.

Revision ID: 0028_agent_conversation_messages
Revises: 0027_graph_invocation_output
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import context, op

revision: str = "0028_agent_conversation_messages"
down_revision: str | Sequence[str] | None = "0027_graph_invocation_output"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.is_offline_mode():
        inspector = inspect(op.get_bind())
        if "agent_conversation_messages" in inspector.get_table_names():
            return
    op.create_table(
        "agent_conversation_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_agent_conversation_message_sequence",
        ),
    )
    op.create_index(
        "ix_agent_conversation_messages_conversation_id",
        "agent_conversation_messages",
        ["conversation_id"],
    )
    op.create_index(
        "ix_agent_conversation_messages_tenant_id",
        "agent_conversation_messages",
        ["tenant_id"],
    )


def downgrade() -> None:
    if not context.is_offline_mode():
        inspector = inspect(op.get_bind())
        if "agent_conversation_messages" not in inspector.get_table_names():
            return
    op.drop_index(
        "ix_agent_conversation_messages_tenant_id",
        table_name="agent_conversation_messages",
    )
    op.drop_index(
        "ix_agent_conversation_messages_conversation_id",
        table_name="agent_conversation_messages",
    )
    op.drop_table("agent_conversation_messages")

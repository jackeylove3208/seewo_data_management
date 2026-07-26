"""Add atomic conversation reset identity and one-active-chat invariant.

Revision ID: 0033_conversation_reset
Revises: 0032_source_storage_ownership
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033_conversation_reset"
down_revision: str | Sequence[str] | None = "0032_source_storage_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column("reset_idempotency_key", sa.String(length=128), nullable=True),
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    conversation.id,
                    row_number() OVER (
                        PARTITION BY conversation.tenant_id, conversation.created_by
                        ORDER BY
                            CASE WHEN EXISTS (
                                SELECT 1
                                FROM agent_runs AS run
                                WHERE run.conversation_id = conversation.id
                                  AND run.status NOT IN ('completed', 'terminated', 'failed')
                            ) THEN 0 ELSE 1 END,
                            conversation.created_at DESC,
                            conversation.id DESC
                    ) AS position
                FROM agent_conversations AS conversation
                WHERE conversation.status = 'active'
            )
            DELETE FROM agent_conversations
            WHERE id IN (
                SELECT id FROM ranked WHERE position > 1
            )
            """
        )
    )
    op.create_unique_constraint(
        "uq_agent_conversation_reset_key",
        "agent_conversations",
        ["tenant_id", "created_by", "reset_idempotency_key"],
    )
    op.create_index(
        "uq_agent_conversations_active_operator",
        "agent_conversations",
        ["tenant_id", "created_by"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_conversations_active_operator",
        table_name="agent_conversations",
    )
    op.drop_constraint(
        "uq_agent_conversation_reset_key",
        "agent_conversations",
        type_="unique",
    )
    op.drop_column("agent_conversations", "reset_idempotency_key")

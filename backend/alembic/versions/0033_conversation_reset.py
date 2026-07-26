"""Add atomic conversation reset identity and one-active-chat invariant.

Revision ID: 0033_conversation_reset
Revises: 0032_source_storage_ownership
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0033_conversation_reset"
down_revision: str | Sequence[str] | None = "0032_source_storage_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column(
            "agent_conversations",
            sa.Column("reset_idempotency_key", sa.String(length=128), nullable=True),
        )
        _deduplicate_active_conversations()
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
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {
        column["name"] for column in inspector.get_columns("agent_conversations")
    }
    index_names = {
        index["name"]
        for index in inspector.get_indexes("agent_conversations")
        if index.get("name")
    }
    constraint_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("agent_conversations")
        if constraint.get("name")
    }

    if "reset_idempotency_key" not in column_names:
        op.add_column(
            "agent_conversations",
            sa.Column("reset_idempotency_key", sa.String(length=128), nullable=True),
        )

    _deduplicate_active_conversations()

    if "uq_agent_conversation_reset_key" not in index_names | constraint_names:
        if bind.dialect.name == "sqlite":
            op.create_index(
                "uq_agent_conversation_reset_key",
                "agent_conversations",
                ["tenant_id", "created_by", "reset_idempotency_key"],
                unique=True,
            )
        else:
            op.create_unique_constraint(
                "uq_agent_conversation_reset_key",
                "agent_conversations",
                ["tenant_id", "created_by", "reset_idempotency_key"],
            )

    if "uq_agent_conversations_active_operator" not in index_names:
        op.create_index(
            "uq_agent_conversations_active_operator",
            "agent_conversations",
            ["tenant_id", "created_by"],
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
            sqlite_where=sa.text("status = 'active'"),
        )


def _deduplicate_active_conversations() -> None:
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


def downgrade() -> None:
    if context.is_offline_mode():
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
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {
        column["name"] for column in inspector.get_columns("agent_conversations")
    }
    index_names = {
        index["name"]
        for index in inspector.get_indexes("agent_conversations")
        if index.get("name")
    }
    constraint_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("agent_conversations")
        if constraint.get("name")
    }

    if "uq_agent_conversations_active_operator" in index_names:
        op.drop_index(
            "uq_agent_conversations_active_operator",
            table_name="agent_conversations",
        )
    if "uq_agent_conversation_reset_key" in index_names:
        op.drop_index(
            "uq_agent_conversation_reset_key",
            table_name="agent_conversations",
        )

    reset_constraint_exists = (
        "uq_agent_conversation_reset_key" in constraint_names
    )
    if bind.dialect.name == "sqlite" and reset_constraint_exists:
        with op.batch_alter_table("agent_conversations") as batch_op:
            batch_op.drop_constraint(
                "uq_agent_conversation_reset_key",
                type_="unique",
            )
            if "reset_idempotency_key" in column_names:
                batch_op.drop_column("reset_idempotency_key")
        return

    if reset_constraint_exists:
        op.drop_constraint(
            "uq_agent_conversation_reset_key",
            "agent_conversations",
            type_="unique",
        )
    if "reset_idempotency_key" in column_names:
        op.drop_column("agent_conversations", "reset_idempotency_key")

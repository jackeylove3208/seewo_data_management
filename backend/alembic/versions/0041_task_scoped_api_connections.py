"""Bind conversation API credentials to one task and revoke them safely.

Revision ID: 0041_task_scoped_api_connections
Revises: 0040_mapping_hash_widths
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0041_task_scoped_api_connections"
down_revision: str | Sequence[str] | None = "0040_mapping_hash_widths"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.is_offline_mode():
        inspector = sa.inspect(op.get_bind())
        configuration_columns = {
            column["name"]
            for column in inspector.get_columns("api_configuration_sessions")
        }
        connection_columns = {
            column["name"] for column in inspector.get_columns("api_connections")
        }
        if {
            "conversation_id",
            "created_by",
            "connection_scope",
        } <= configuration_columns and {
            "scope",
            "conversation_id",
            "task_id",
            "credentials_revoked_at",
            "disabled_reason",
        } <= connection_columns:
            return

    with op.batch_alter_table("api_configuration_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("conversation_id", sa.Uuid(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("created_by", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "connection_scope",
                sa.String(length=32),
                server_default="persistent",
                nullable=False,
            )
        )
        batch_op.create_foreign_key(
            "fk_api_configuration_sessions_conversation_id",
            "agent_conversations",
            ["conversation_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            "ix_api_configuration_sessions_conversation_id",
            ["conversation_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_api_configuration_sessions_created_by",
            ["created_by"],
            unique=False,
        )

    with op.batch_alter_table("api_connections") as batch_op:
        batch_op.add_column(
            sa.Column(
                "scope",
                sa.String(length=32),
                server_default="persistent",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("conversation_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("task_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("credentials_revoked_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("disabled_reason", sa.String(length=64), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_api_connections_conversation_id",
            "agent_conversations",
            ["conversation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_api_connections_task_id",
            "reconciliation_tasks",
            ["task_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_api_connections_scope", ["scope"], unique=False)
        batch_op.create_index(
            "ix_api_connections_conversation_id",
            ["conversation_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_api_connections_task_id",
            ["task_id"],
            unique=True,
        )
        batch_op.create_check_constraint(
            "ck_api_connection_scope",
            "scope IN ('persistent', 'task_ephemeral')",
        )
        batch_op.create_check_constraint(
            "ck_api_connection_scope_binding",
            "(scope = 'persistent' AND conversation_id IS NULL AND task_id IS NULL) "
            "OR (scope = 'task_ephemeral' AND "
            "(conversation_id IS NOT NULL OR credentials_revoked_at IS NOT NULL))",
        )
        batch_op.create_check_constraint(
            "ck_api_connection_revoked_state",
            "credentials_revoked_at IS NULL OR state = 'disabled'",
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE api_connections SET state = 'disabled' "
            "WHERE scope = 'task_ephemeral'"
        )
    )
    with op.batch_alter_table("api_connections") as batch_op:
        batch_op.drop_constraint(
            "ck_api_connection_revoked_state",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_api_connection_scope_binding",
            type_="check",
        )
        batch_op.drop_constraint("ck_api_connection_scope", type_="check")
        batch_op.drop_index("ix_api_connections_task_id")
        batch_op.drop_index("ix_api_connections_conversation_id")
        batch_op.drop_index("ix_api_connections_scope")
        batch_op.drop_constraint(
            "fk_api_connections_task_id",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_api_connections_conversation_id",
            type_="foreignkey",
        )
        batch_op.drop_column("disabled_reason")
        batch_op.drop_column("credentials_revoked_at")
        batch_op.drop_column("task_id")
        batch_op.drop_column("conversation_id")
        batch_op.drop_column("scope")

    with op.batch_alter_table("api_configuration_sessions") as batch_op:
        batch_op.drop_index("ix_api_configuration_sessions_created_by")
        batch_op.drop_index("ix_api_configuration_sessions_conversation_id")
        batch_op.drop_constraint(
            "fk_api_configuration_sessions_conversation_id",
            type_="foreignkey",
        )
        batch_op.drop_column("connection_scope")
        batch_op.drop_column("created_by")
        batch_op.drop_column("conversation_id")

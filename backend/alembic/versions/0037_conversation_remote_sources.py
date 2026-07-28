"""Add conversation-bound remote CSV source records.

Revision ID: 0037_conversation_remote_sources
Revises: 0036_agent_rollback_cycles
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0037_conversation_remote_sources"
down_revision: str | Sequence[str] | None = "0036_agent_rollback_cycles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.is_offline_mode():
        tables = set(sa.inspect(op.get_bind()).get_table_names())
        if "remote_sources" in tables:
            return
    op.create_table(
        "remote_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("source_file_id", sa.Uuid(), nullable=True),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("display_origin", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("media_type", sa.String(length=255), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_problem_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["reconciliation_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_file_id"],
            ["source_files.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns, unique in (
        ("ix_remote_sources_tenant_id", ["tenant_id"], False),
        ("ix_remote_sources_created_by", ["created_by"], False),
        ("ix_remote_sources_conversation_id", ["conversation_id"], False),
        ("uq_remote_sources_task_id", ["task_id"], True),
        ("uq_remote_sources_source_file_id", ["source_file_id"], True),
    ):
        op.create_index(name, "remote_sources", columns, unique=unique)


def downgrade() -> None:
    if not context.is_offline_mode():
        tables = set(sa.inspect(op.get_bind()).get_table_names())
        if "remote_sources" not in tables:
            return
    for name in (
        "uq_remote_sources_source_file_id",
        "uq_remote_sources_task_id",
        "ix_remote_sources_conversation_id",
        "ix_remote_sources_created_by",
        "ix_remote_sources_tenant_id",
    ):
        op.drop_index(name, table_name="remote_sources")
    op.drop_table("remote_sources")

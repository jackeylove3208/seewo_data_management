"""Expand remote source-file storage names.

Revision ID: 0038_expand_storage_name
Revises: 0037_conversation_remote_sources
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038_expand_storage_name"
down_revision: str | Sequence[str] | None = "0037_conversation_remote_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_files") as batch_op:
        batch_op.alter_column(
            "storage_name",
            existing_type=sa.String(length=80),
            type_=sa.String(length=128),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("source_files") as batch_op:
        batch_op.alter_column(
            "storage_name",
            existing_type=sa.String(length=128),
            type_=sa.String(length=80),
            existing_nullable=False,
        )

"""Allow prefixed SHA-256 hashes in durable Agent checkpoints.

Revision ID: 0030_agent_checkpoint_hash
Revises: 0029_agent_analysis_task_delete
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_agent_checkpoint_hash"
down_revision: str | Sequence[str] | None = "0029_agent_analysis_task_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_checkpoints") as batch_op:
        batch_op.alter_column(
            "input_hash",
            existing_type=sa.String(length=64),
            type_=sa.String(length=71),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_checkpoints") as batch_op:
        batch_op.alter_column(
            "input_hash",
            existing_type=sa.String(length=71),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

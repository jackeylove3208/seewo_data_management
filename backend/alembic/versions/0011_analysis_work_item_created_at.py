"""Add the missing analysis work item creation timestamp.

Revision ID: 0011_analysis_work_item_created_at
Revises: 0010_durable_analysis_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0011_analysis_work_item_created_at"
down_revision: str | None = "0010_durable_analysis_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if _has_created_at_column():
        return
    op.add_column(
        "analysis_work_items",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text("UPDATE analysis_work_items SET created_at = available_at WHERE created_at IS NULL")
    )
    with op.batch_alter_table("analysis_work_items") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )


def downgrade() -> None:
    if not context.is_offline_mode() and not _has_created_at_column():
        return
    with op.batch_alter_table("analysis_work_items") as batch_op:
        batch_op.drop_column("created_at")


def _has_created_at_column() -> bool:
    if context.is_offline_mode():
        return False
    inspector = sa.inspect(op.get_bind())
    if "analysis_work_items" not in inspector.get_table_names():
        return False
    return "created_at" in {
        column["name"] for column in inspector.get_columns("analysis_work_items")
    }

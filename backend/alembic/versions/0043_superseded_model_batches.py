"""Allow oversized model batches to be superseded during repartitioning.

Revision ID: 0043_superseded_model_batches
Revises: 0042_csv_database_bindings
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0043_superseded_model_batches"
down_revision: str | Sequence[str] | None = "0042_csv_database_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQLITE_CHILD_DELETE_OWNERSHIP = {
    "agent_model_batch_items": (
        "EXISTS (SELECT 1 FROM agent_model_batches parent "
        "WHERE parent.id = OLD.batch_id "
        "AND parent.task_id = agent_task_deletion_guard.task_id)"
    ),
    "agent_model_attempts": (
        "EXISTS (SELECT 1 FROM agent_model_batches parent "
        "WHERE parent.id = OLD.batch_id "
        "AND parent.task_id = agent_task_deletion_guard.task_id)"
    ),
}


def _drop_sqlite_child_delete_triggers() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for table in _SQLITE_CHILD_DELETE_OWNERSHIP:
        op.execute(f"DROP TRIGGER IF EXISTS reject_{table}_delete")


def _restore_sqlite_child_delete_triggers() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for table, ownership in _SQLITE_CHILD_DELETE_OWNERSHIP.items():
        op.execute(
            f"""
            CREATE TRIGGER reject_{table}_delete
            BEFORE DELETE ON {table}
            WHEN NOT EXISTS (
                SELECT 1
                FROM agent_task_deletion_guard
                WHERE id = 1
                  AND task_id IS NOT NULL
                  AND ({ownership})
            )
            BEGIN SELECT RAISE(
                ABORT,
                'new-Agent analysis records are append-only'
            ); END
            """
        )


def upgrade() -> None:
    _drop_sqlite_child_delete_triggers()
    with op.batch_alter_table("agent_model_batches") as batch_op:
        batch_op.drop_constraint("ck_agent_batch_status", type_="check")
        batch_op.create_check_constraint(
            "ck_agent_batch_status",
            "status IN ('pending', 'claimed', 'completed', 'blocked', 'superseded')",
        )
    _restore_sqlite_child_delete_triggers()


def downgrade() -> None:
    superseded_count = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM agent_model_batches WHERE status = 'superseded'")
    )
    if superseded_count:
        raise RuntimeError(
            "Cannot downgrade while superseded model batches exist; "
            "archive or migrate those tasks first"
        )
    _drop_sqlite_child_delete_triggers()
    with op.batch_alter_table("agent_model_batches") as batch_op:
        batch_op.drop_constraint("ck_agent_batch_status", type_="check")
        batch_op.create_check_constraint(
            "ck_agent_batch_status",
            "status IN ('pending', 'claimed', 'completed', 'blocked')",
        )
    _restore_sqlite_child_delete_triggers()

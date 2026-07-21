"""Allow task deletion to remove unexecuted target versions.

Revision ID: 0017_task_delete_target_version
Revises: 0016_task_delete_pre_execution
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_task_delete_target_version"
down_revision: str | None = "0016_task_delete_pre_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER reject_target_versions_mutation ON target_versions")
        op.execute(
            """
            CREATE TRIGGER reject_target_versions_mutation
            BEFORE UPDATE OR DELETE ON target_versions
            FOR EACH ROW EXECUTE FUNCTION allow_task_deletion_mutation_fn()
            """
        )
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS reject_target_versions_delete")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER reject_target_versions_mutation ON target_versions")
        op.execute(
            """
            CREATE TRIGGER reject_target_versions_mutation
            BEFORE UPDATE OR DELETE ON target_versions
            FOR EACH ROW EXECUTE FUNCTION reject_execution_history_mutation_fn()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER reject_target_versions_delete
            BEFORE DELETE ON target_versions
            BEGIN
                SELECT RAISE(ABORT, 'target_versions records are immutable');
            END
            """
        )

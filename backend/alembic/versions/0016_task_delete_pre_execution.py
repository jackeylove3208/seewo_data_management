"""Allow deletion of unexecuted task planning data.

Revision ID: 0016_task_delete_pre_execution
Revises: 0015_merge_reporting_matching
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_task_delete_pre_execution"
down_revision: str | None = "0015_merge_reporting_matching"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _upgrade_postgresql()
    elif dialect == "sqlite":
        _upgrade_sqlite()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _downgrade_postgresql()
    elif dialect == "sqlite":
        _downgrade_sqlite()


def _upgrade_postgresql() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION allow_task_deletion_mutation_fn()
        RETURNS trigger AS $$
        BEGIN
            IF current_setting('app.task_deletion', true) = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION '% records are immutable', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("governance_proposals", "governance_plans", "governance_plan_explanations"):
        op.execute(f"DROP TRIGGER IF EXISTS reject_{table}_mutation ON {table}")
        op.execute(
            f"""
            CREATE TRIGGER reject_{table}_mutation
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION allow_task_deletion_mutation_fn()
            """
        )


def _downgrade_postgresql() -> None:
    for table in ("governance_proposals", "governance_plans", "governance_plan_explanations"):
        op.execute(f"DROP TRIGGER reject_{table}_mutation ON {table}")
    op.execute("DROP FUNCTION allow_task_deletion_mutation_fn()")


def _upgrade_sqlite() -> None:
    for table in ("governance_proposals", "governance_plans", "governance_plan_explanations"):
        op.execute(f"DROP TRIGGER IF EXISTS reject_{table}_delete")


def _downgrade_sqlite() -> None:
    for table, message in (
        ("governance_proposals", "governance_proposals are immutable"),
        ("governance_plans", "governance_plans records are immutable"),
        ("governance_plan_explanations", "governance plan explanations are immutable"),
    ):
        op.execute(
            f"""
            CREATE TRIGGER reject_{table}_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{message}');
            END
            """
        )

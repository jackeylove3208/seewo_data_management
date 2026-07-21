"""Guard AI governance analysis history against database-level mutation.

Revision ID: 0006_analysis_immutability
Revises: 0005_analysis_results
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_analysis_immutability"
down_revision: str | None = "0005_analysis_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS reject_analysis_results_mutation ON analysis_results"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_analysis_results_mutation_fn()")
        op.execute(
            """
            CREATE FUNCTION reject_analysis_results_mutation_fn()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'analysis_results are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER reject_analysis_results_mutation
            BEFORE UPDATE OR DELETE ON analysis_results
            FOR EACH ROW EXECUTE FUNCTION reject_analysis_results_mutation_fn()
            """
        )
    elif op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS reject_analysis_results_update")
        op.execute("DROP TRIGGER IF EXISTS reject_analysis_results_delete")
        op.execute(
            """
            CREATE TRIGGER reject_analysis_results_update
            BEFORE UPDATE ON analysis_results
            BEGIN
                SELECT RAISE(ABORT, 'analysis_results are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER reject_analysis_results_delete
            BEFORE DELETE ON analysis_results
            BEGIN
                SELECT RAISE(ABORT, 'analysis_results are immutable');
            END
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER reject_analysis_results_mutation ON analysis_results")
        op.execute("DROP FUNCTION reject_analysis_results_mutation_fn()")
    elif op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER reject_analysis_results_update")
        op.execute("DROP TRIGGER reject_analysis_results_delete")

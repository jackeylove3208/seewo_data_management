"""Persist optional governance plan explanations separately.

Revision ID: 0011_plan_explanations
Revises: 0010_governance_execution
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0011_plan_explanations"
down_revision: str | None = "0010_governance_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if (
        not context.is_offline_mode()
        and "governance_plan_explanations" in sa.inspect(op.get_bind()).get_table_names()
    ):
        return
    op.create_table(
        "governance_plan_explanations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["governance_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_governance_plan_explanations_plan_id",
        "governance_plan_explanations",
        ["plan_id"],
    )
    op.create_index(
        "ix_governance_plan_explanations_request_id",
        "governance_plan_explanations",
        ["request_id"],
        unique=True,
    )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE TRIGGER reject_governance_plan_explanations_mutation
            BEFORE UPDATE OR DELETE ON governance_plan_explanations
            FOR EACH ROW EXECUTE FUNCTION reject_execution_history_mutation_fn()
            """
        )
    elif dialect == "sqlite":
        for action in ("update", "delete"):
            op.execute(
                f"""
                CREATE TRIGGER reject_governance_plan_explanations_{action}
                BEFORE {action.upper()} ON governance_plan_explanations
                BEGIN
                    SELECT RAISE(ABORT, 'governance plan explanations are immutable');
                END
                """
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER reject_governance_plan_explanations_mutation "
            "ON governance_plan_explanations"
        )
    elif dialect == "sqlite":
        for action in ("update", "delete"):
            op.execute(f"DROP TRIGGER reject_governance_plan_explanations_{action}")
    op.drop_index(
        "ix_governance_plan_explanations_request_id",
        table_name="governance_plan_explanations",
    )
    op.drop_index(
        "ix_governance_plan_explanations_plan_id",
        table_name="governance_plan_explanations",
    )
    op.drop_table("governance_plan_explanations")

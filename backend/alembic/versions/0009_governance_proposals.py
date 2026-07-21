"""Create immutable pending governance proposals.

Revision ID: 0009_governance_proposals
Revises: 0008_analysis_gateway_requests
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0009_governance_proposals"
down_revision: str | None = "0008_analysis_gateway_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUIRED_COLUMNS = frozenset(
    {
        "id",
        "task_id",
        "tenant_id",
        "difference_id",
        "difference_version",
        "analysis_id",
        "analysis_version",
        "proposal_version",
        "proposal_source",
        "operation_type",
        "target_entity_id",
        "changes",
        "rationale",
        "evidence_refs",
        "risk",
        "created_by",
        "created_at",
        "status",
        "supersedes_id",
    }
)
_REQUIRED_UNIQUE = {
    "uq_governance_proposal_version": (
        "difference_id",
        "difference_version",
        "proposal_version",
    ),
}
_REQUIRED_INDEXES = {
    "ix_governance_proposals_difference_id": ("difference_id",),
    "ix_governance_proposals_task_id": ("task_id",),
    "ix_governance_proposals_tenant_id": ("tenant_id",),
    "ix_governance_proposals_analysis_id": ("analysis_id",),
    "ix_governance_proposals_created_by": ("created_by",),
    "ix_governance_proposals_status": ("status",),
}


def upgrade() -> None:
    if not context.is_offline_mode() and sa.inspect(op.get_bind()).has_table(
        "governance_proposals"
    ):
        _validate_and_repair_existing_table()
        _create_immutability_guard()
        return
    op.create_table(
        "governance_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("difference_id", sa.Uuid(), nullable=False),
        sa.Column("difference_version", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_version", sa.String(length=64), nullable=False),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("proposal_source", sa.String(length=32), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("target_entity_id", sa.Uuid(), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.String(length=2000), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("risk", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["analysis_id"], ["analysis_results.id"]),
        sa.ForeignKeyConstraint(["difference_id"], ["difference_items.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["governance_proposals.id"]),
        sa.ForeignKeyConstraint(["target_entity_id"], ["canonical_entities.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "difference_id",
            "difference_version",
            "proposal_version",
            name="uq_governance_proposal_version",
        ),
    )
    op.create_index(
        "ix_governance_proposals_difference_id",
        "governance_proposals",
        ["difference_id"],
    )
    op.create_index("ix_governance_proposals_task_id", "governance_proposals", ["task_id"])
    op.create_index("ix_governance_proposals_tenant_id", "governance_proposals", ["tenant_id"])
    op.create_index("ix_governance_proposals_analysis_id", "governance_proposals", ["analysis_id"])
    op.create_index("ix_governance_proposals_created_by", "governance_proposals", ["created_by"])
    op.create_index("ix_governance_proposals_status", "governance_proposals", ["status"])
    _create_immutability_guard()


def _validate_and_repair_existing_table() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("governance_proposals")}
    missing_columns = sorted(_REQUIRED_COLUMNS - columns)
    if missing_columns:
        raise RuntimeError(
            "governance_proposals schema is incomplete; missing columns: "
            + ", ".join(missing_columns)
        )
    unique_constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("governance_proposals")
    }
    for name, expected_columns in _REQUIRED_UNIQUE.items():
        if unique_constraints.get(name) != expected_columns:
            raise RuntimeError(
                f"governance_proposals is missing required unique constraint {name}"
            )
    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("governance_proposals")
    }
    for name, expected_columns in _REQUIRED_INDEXES.items():
        actual_columns = indexes.get(name)
        if actual_columns is None:
            op.create_index(name, "governance_proposals", list(expected_columns))
        elif actual_columns != expected_columns:
            raise RuntimeError(
                f"governance_proposals index {name} has unexpected columns"
            )


def downgrade() -> None:
    _drop_immutability_guard()
    op.drop_index("ix_governance_proposals_status", table_name="governance_proposals")
    op.drop_index("ix_governance_proposals_created_by", table_name="governance_proposals")
    op.drop_index("ix_governance_proposals_analysis_id", table_name="governance_proposals")
    op.drop_index("ix_governance_proposals_tenant_id", table_name="governance_proposals")
    op.drop_index("ix_governance_proposals_task_id", table_name="governance_proposals")
    op.drop_index("ix_governance_proposals_difference_id", table_name="governance_proposals")
    op.drop_table("governance_proposals")


def _create_immutability_guard() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS reject_governance_proposals_mutation "
            "ON governance_proposals"
        )
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_governance_proposals_mutation_fn()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'governance_proposals are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER reject_governance_proposals_mutation
            BEFORE UPDATE OR DELETE ON governance_proposals
            FOR EACH ROW EXECUTE FUNCTION reject_governance_proposals_mutation_fn()
            """
        )
    elif op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS reject_governance_proposals_update")
        op.execute("DROP TRIGGER IF EXISTS reject_governance_proposals_delete")
        op.execute(
            """
            CREATE TRIGGER reject_governance_proposals_update
            BEFORE UPDATE ON governance_proposals
            BEGIN
                SELECT RAISE(ABORT, 'governance_proposals are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER reject_governance_proposals_delete
            BEFORE DELETE ON governance_proposals
            BEGIN
                SELECT RAISE(ABORT, 'governance_proposals are immutable');
            END
            """
        )


def _drop_immutability_guard() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER reject_governance_proposals_mutation ON governance_proposals")
        op.execute("DROP FUNCTION reject_governance_proposals_mutation_fn()")
    elif op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER reject_governance_proposals_update")
        op.execute("DROP TRIGGER reject_governance_proposals_delete")

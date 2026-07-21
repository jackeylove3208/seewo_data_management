"""Create immutable governance execution history.

Revision ID: 0010_governance_execution
Revises: 0009_governance_proposals
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_governance_execution"
down_revision: str | None = "0009_governance_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_TABLES = (
    "governance_plans",
    "execution_batches",
    "execution_operations",
    "operation_attempts",
    "target_versions",
    "execution_audit_events",
)


def upgrade() -> None:
    op.create_table(
        "governance_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("target_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("target_version", sa.String(length=128), nullable=False),
        sa.Column("proposal_versions", sa.JSON(), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_governance_plan_version"),
        sa.CheckConstraint("length(content_hash) = 64", name="ck_governance_plan_hash"),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["snapshots.id"]),
        sa.ForeignKeyConstraint(["target_snapshot_id"], ["snapshots.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "content_hash", name="uq_governance_plan_content"),
    )
    op.create_index("ix_governance_plans_task_id", "governance_plans", ["task_id"])
    op.create_index(
        "ix_governance_plans_source_snapshot_id",
        "governance_plans",
        ["source_snapshot_id"],
    )
    op.create_index(
        "ix_governance_plans_target_snapshot_id",
        "governance_plans",
        ["target_snapshot_id"],
    )
    op.create_index("ix_governance_plans_created_by", "governance_plans", ["created_by"])

    op.create_table(
        "execution_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("input_target_version_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confirmed_by", sa.String(length=128), nullable=False),
        sa.Column("independent_reviewer_id", sa.String(length=128), nullable=True),
        sa.Column("high_risk_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("preflight_result", sa.JSON(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("plan_version >= 1", name="ck_execution_batch_plan_version"),
        sa.CheckConstraint("status = 'confirmed'", name="ck_execution_batch_status"),
        sa.ForeignKeyConstraint(["plan_id"], ["governance_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_execution_batches_plan_id", "execution_batches", ["plan_id"])
    op.create_index(
        "ix_execution_batches_input_target_version_id",
        "execution_batches",
        ["input_target_version_id"],
    )
    op.create_index(
        "ix_execution_batches_idempotency_key",
        "execution_batches",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index("ix_execution_batches_status", "execution_batches", ["status"])
    op.create_index("ix_execution_batches_confirmed_by", "execution_batches", ["confirmed_by"])
    op.create_index(
        "ix_execution_batches_independent_reviewer_id",
        "execution_batches",
        ["independent_reviewer_id"],
    )

    op.create_table(
        "execution_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("proposal_source", sa.String(length=32), nullable=False),
        sa.Column("difference_id", sa.Uuid(), nullable=False),
        sa.Column("difference_version", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_version", sa.String(length=64), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("target_entity_id", sa.Uuid(), nullable=True),
        sa.Column("target_source_identifier", sa.String(length=255), nullable=True),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("reversible", sa.Boolean(), nullable=False),
        sa.Column("risk", sa.String(length=32), nullable=False),
        sa.Column("compensation_for", sa.Uuid(), nullable=True),
        sa.Column("restore_absence", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("proposal_version >= 1", name="ck_execution_operation_proposal_version"),
        sa.CheckConstraint(
            "difference_version >= 1", name="ck_execution_operation_difference_version"
        ),
        sa.CheckConstraint(
            "proposal_source IN ('ai', 'operator')",
            name="ck_execution_operation_proposal_source",
        ),
        sa.CheckConstraint(
            "operation_type IN ('create', 'update', 'move', 'disable', 'skip')",
            name="ck_execution_operation_type",
        ),
        sa.CheckConstraint("risk IN ('low', 'medium', 'high')", name="ck_execution_operation_risk"),
        sa.ForeignKeyConstraint(["analysis_id"], ["analysis_results.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["execution_batches.id"]),
        sa.ForeignKeyConstraint(["difference_id"], ["difference_items.id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["governance_proposals.id"]),
        sa.ForeignKeyConstraint(["target_entity_id"], ["canonical_entities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "operation_id", name="uq_execution_batch_operation"),
    )
    for column in (
        "batch_id",
        "operation_id",
        "proposal_id",
        "difference_id",
        "analysis_id",
        "operation_type",
        "entity_type",
    ):
        op.create_index(f"ix_execution_operations_{column}", "execution_operations", [column])

    op.create_table(
        "target_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("source_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(file_sha256) = 64", name="ck_target_version_file_hash"),
        sa.CheckConstraint("length(content_hash) = 64", name="ck_target_version_content_hash"),
        sa.ForeignKeyConstraint(["batch_id"], ["execution_batches.id"]),
        sa.ForeignKeyConstraint(["parent_version_id"], ["target_versions.id"]),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["snapshots.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_path"),
    )
    for column in (
        "parent_version_id",
        "task_id",
        "tenant_id",
        "source_snapshot_id",
        "file_sha256",
        "content_hash",
    ):
        op.create_index(f"ix_target_versions_{column}", "target_versions", [column])
    op.create_index(
        "ix_target_versions_batch_id",
        "target_versions",
        ["batch_id"],
        unique=False,
    )

    op.create_table(
        "operation_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.JSON(), nullable=True),
        sa.Column("actual_after", sa.JSON(), nullable=True),
        sa.Column("verification", sa.JSON(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("target_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_number >= 1", name="ck_operation_attempt_number"),
        sa.CheckConstraint(
            "status IN ('pending', 'blocked', 'running', 'succeeded', 'failed', "
            "'verification_failed')",
            name="ck_operation_attempt_status",
        ),
        sa.ForeignKeyConstraint(["operation_id"], ["execution_operations.id"]),
        sa.ForeignKeyConstraint(["target_version_id"], ["target_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "attempt_number", name="uq_operation_attempt_number"),
    )
    for column in (
        "operation_id",
        "status",
        "error_code",
        "retryable",
        "target_version_id",
    ):
        op.create_index(f"ix_operation_attempts_{column}", "operation_attempts", [column])

    op.create_table(
        "execution_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["execution_batches.id"]),
        sa.ForeignKeyConstraint(["operation_id"], ["execution_operations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("batch_id", "operation_id", "actor_id", "event_type"):
        op.create_index(f"ix_execution_audit_events_{column}", "execution_audit_events", [column])

    _create_immutability_guards()


def downgrade() -> None:
    _drop_immutability_guards()
    for column in ("event_type", "actor_id", "operation_id", "batch_id"):
        op.drop_index(
            f"ix_execution_audit_events_{column}",
            table_name="execution_audit_events",
        )
    op.drop_table("execution_audit_events")
    for column in (
        "target_version_id",
        "retryable",
        "error_code",
        "status",
        "operation_id",
    ):
        op.drop_index(f"ix_operation_attempts_{column}", table_name="operation_attempts")
    op.drop_table("operation_attempts")
    op.drop_index("ix_target_versions_batch_id", table_name="target_versions")
    for column in (
        "content_hash",
        "file_sha256",
        "source_snapshot_id",
        "tenant_id",
        "task_id",
        "parent_version_id",
    ):
        op.drop_index(f"ix_target_versions_{column}", table_name="target_versions")
    op.drop_table("target_versions")
    for column in (
        "entity_type",
        "operation_type",
        "analysis_id",
        "difference_id",
        "proposal_id",
        "operation_id",
        "batch_id",
    ):
        op.drop_index(f"ix_execution_operations_{column}", table_name="execution_operations")
    op.drop_table("execution_operations")
    op.drop_index("ix_execution_batches_independent_reviewer_id", table_name="execution_batches")
    op.drop_index("ix_execution_batches_confirmed_by", table_name="execution_batches")
    op.drop_index("ix_execution_batches_status", table_name="execution_batches")
    op.drop_index("ix_execution_batches_idempotency_key", table_name="execution_batches")
    op.drop_index("ix_execution_batches_input_target_version_id", table_name="execution_batches")
    op.drop_index("ix_execution_batches_plan_id", table_name="execution_batches")
    op.drop_table("execution_batches")
    op.drop_index("ix_governance_plans_created_by", table_name="governance_plans")
    op.drop_index("ix_governance_plans_target_snapshot_id", table_name="governance_plans")
    op.drop_index("ix_governance_plans_source_snapshot_id", table_name="governance_plans")
    op.drop_index("ix_governance_plans_task_id", table_name="governance_plans")
    op.drop_table("governance_plans")


def _create_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_execution_history_mutation_fn()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% records are immutable', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table in IMMUTABLE_TABLES:
            op.execute(
                f"""
                CREATE TRIGGER reject_{table}_mutation
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION reject_execution_history_mutation_fn()
                """
            )
    elif dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            op.execute(
                f"""
                CREATE TRIGGER reject_{table}_update
                BEFORE UPDATE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{table} records are immutable');
                END
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER reject_{table}_delete
                BEFORE DELETE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{table} records are immutable');
                END
                """
            )


def _drop_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table in IMMUTABLE_TABLES:
            op.execute(f"DROP TRIGGER reject_{table}_mutation ON {table}")
        op.execute("DROP FUNCTION reject_execution_history_mutation_fn()")
    elif dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            op.execute(f"DROP TRIGGER reject_{table}_update")
            op.execute(f"DROP TRIGGER reject_{table}_delete")

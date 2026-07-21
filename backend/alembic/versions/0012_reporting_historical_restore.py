"""reporting and historical restore

Revision ID: 0012_reporting_restore
Revises: 0011_plan_explanations
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0012_reporting_restore"
down_revision: str | None = "0011_plan_explanations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_TABLES = (
    "governance_reports",
    "restore_requests",
    "restore_execution_links",
    "restore_execution_results",
)


def upgrade() -> None:
    existing_tables = set() if context.is_offline_mode() else set(
        sa.inspect(op.get_bind()).get_table_names()
    )
    if "report_jobs" in existing_tables:
        return
    op.create_table(
        "report_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("execution_id", sa.Uuid(), sa.ForeignKey("execution_batches.id"), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("facts_hash", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("execution_id", "version", name="uq_report_job_version"),
        sa.UniqueConstraint(
            "tenant_id", "execution_id", "idempotency_key", name="uq_report_job_idempotency"
        ),
    )
    for column in ("execution_id", "tenant_id", "status", "requested_by"):
        op.create_index(f"ix_report_jobs_{column}", "report_jobs", [column])
    op.create_table(
        "governance_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("report_jobs.id"), nullable=False, unique=True
        ),
        sa.Column("execution_id", sa.Uuid(), sa.ForeignKey("execution_batches.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("facts_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("html_content", sa.Text(), nullable=False),
        sa.Column("html_hash", sa.String(64), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("generated_by", sa.String(128), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("execution_id", "version", name="uq_governance_report_version"),
    )
    op.create_index("ix_governance_reports_job_id", "governance_reports", ["job_id"], unique=True)
    for column in ("execution_id", "generated_by"):
        op.create_index(f"ix_governance_reports_{column}", "governance_reports", [column])
    op.create_table(
        "restore_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("reconciliation_tasks.id"), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column(
            "source_version_id", sa.Uuid(), sa.ForeignKey("target_versions.id"), nullable=False
        ),
        sa.Column(
            "semantic_source_version_id",
            sa.Uuid(),
            sa.ForeignKey("target_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "target_version_id", sa.Uuid(), sa.ForeignKey("target_versions.id"), nullable=False
        ),
        sa.Column("preview_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("deterministic_plan", sa.JSON(), nullable=False),
        sa.Column("covered_execution_ids", sa.JSON(), nullable=False),
        sa.Column("ai_candidate", sa.JSON(), nullable=True),
        sa.Column("ai_provenance", sa.JSON(), nullable=True),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        "task_id",
        "tenant_id",
        "source_version_id",
        "semantic_source_version_id",
        "target_version_id",
        "requested_by",
    ):
        op.create_index(f"ix_restore_requests_{column}", "restore_requests", [column])
    op.create_index(
        "ix_restore_requests_preview_hash",
        "restore_requests",
        ["preview_hash"],
        unique=True,
    )
    op.create_table(
        "restore_execution_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "restore_request_id",
            sa.Uuid(),
            sa.ForeignKey("restore_requests.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "compensation_plan_id", sa.Uuid(), sa.ForeignKey("governance_plans.id"), nullable=False
        ),
        sa.Column(
            "compensation_batch_id",
            sa.Uuid(),
            sa.ForeignKey("execution_batches.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "output_version_id",
            sa.Uuid(),
            sa.ForeignKey("target_versions.id"),
            nullable=True,
            unique=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_restore_execution_links_restore_request_id",
        "restore_execution_links",
        ["restore_request_id"],
        unique=True,
    )
    op.create_index(
        "ix_restore_execution_links_compensation_plan_id",
        "restore_execution_links",
        ["compensation_plan_id"],
    )
    op.create_index(
        "ix_restore_execution_links_compensation_batch_id",
        "restore_execution_links",
        ["compensation_batch_id"],
        unique=True,
    )
    op.create_index(
        "ix_restore_execution_links_output_version_id",
        "restore_execution_links",
        ["output_version_id"],
        unique=True,
    )
    op.create_table(
        "restore_execution_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "restore_execution_link_id",
            sa.Uuid(),
            sa.ForeignKey("restore_execution_links.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "output_version_id",
            sa.Uuid(),
            sa.ForeignKey("target_versions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("verified_content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_restore_execution_results_restore_execution_link_id",
        "restore_execution_results",
        ["restore_execution_link_id"],
        unique=True,
    )
    op.create_index(
        "ix_restore_execution_results_output_version_id",
        "restore_execution_results",
        ["output_version_id"],
        unique=True,
    )
    _create_immutability_guards()


def downgrade() -> None:
    _drop_immutability_guards()
    op.drop_index(
        "ix_restore_execution_results_output_version_id",
        table_name="restore_execution_results",
    )
    op.drop_index(
        "ix_restore_execution_results_restore_execution_link_id",
        table_name="restore_execution_results",
    )
    op.drop_table("restore_execution_results")
    for column in (
        "output_version_id",
        "compensation_batch_id",
        "compensation_plan_id",
        "restore_request_id",
    ):
        op.drop_index(
            f"ix_restore_execution_links_{column}",
            table_name="restore_execution_links",
        )
    op.drop_table("restore_execution_links")
    op.drop_index("ix_restore_requests_preview_hash", table_name="restore_requests")
    for column in (
        "requested_by",
        "target_version_id",
        "semantic_source_version_id",
        "source_version_id",
        "tenant_id",
        "task_id",
    ):
        op.drop_index(f"ix_restore_requests_{column}", table_name="restore_requests")
    op.drop_table("restore_requests")
    for column in ("generated_by", "execution_id", "job_id"):
        op.drop_index(f"ix_governance_reports_{column}", table_name="governance_reports")
    op.drop_table("governance_reports")
    for column in ("requested_by", "status", "tenant_id", "execution_id"):
        op.drop_index(f"ix_report_jobs_{column}", table_name="report_jobs")
    op.drop_table("report_jobs")


def _create_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_report_job_fact_mutation_fn()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' OR NEW.execution_id IS DISTINCT FROM OLD.execution_id
                    OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                    OR NEW.version IS DISTINCT FROM OLD.version
                    OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                    OR NEW.facts IS DISTINCT FROM OLD.facts
                    OR NEW.facts_hash IS DISTINCT FROM OLD.facts_hash
                    OR NEW.requested_by IS DISTINCT FROM OLD.requested_by THEN
                    RAISE EXCEPTION 'report job facts are immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER reject_report_job_fact_mutation
            BEFORE UPDATE OR DELETE ON report_jobs
            FOR EACH ROW EXECUTE FUNCTION reject_report_job_fact_mutation_fn()
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
        op.execute(
            """
            CREATE TRIGGER reject_report_jobs_fact_update
            BEFORE UPDATE OF execution_id, tenant_id, version, idempotency_key,
                facts, facts_hash, requested_by ON report_jobs
            BEGIN
                SELECT RAISE(ABORT, 'report job facts are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER reject_report_jobs_delete
            BEFORE DELETE ON report_jobs
            BEGIN
                SELECT RAISE(ABORT, 'report job facts are immutable');
            END
            """
        )
        for table in IMMUTABLE_TABLES:
            for action in ("update", "delete"):
                op.execute(
                    f"""
                    CREATE TRIGGER reject_{table}_{action}
                    BEFORE {action.upper()} ON {table}
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
        op.execute("DROP TRIGGER reject_report_job_fact_mutation ON report_jobs")
        op.execute("DROP FUNCTION reject_report_job_fact_mutation_fn()")
    elif dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER reject_{table}_{action}")
        op.execute("DROP TRIGGER reject_report_jobs_fact_update")
        op.execute("DROP TRIGGER reject_report_jobs_delete")

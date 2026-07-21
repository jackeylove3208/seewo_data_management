"""Create durable AI analysis jobs and work items.

Revision ID: 0010_durable_analysis_jobs
Revises: 0009_governance_proposals
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0010_durable_analysis_jobs"
down_revision: str | None = "0009_governance_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing_tables = (
        set() if context.is_offline_mode() else set(sa.inspect(op.get_bind()).get_table_names())
    )
    if "analysis_jobs" not in existing_tables:
        op.create_table(
            "analysis_jobs",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("task_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("requested_by", sa.String(length=128), nullable=False),
            sa.Column("analysis_version", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("total", sa.Integer(), nullable=False),
            sa.Column("completed", sa.Integer(), nullable=False),
            sa.Column("succeeded", sa.Integer(), nullable=False),
            sa.Column("manual_required", sa.Integer(), nullable=False),
            sa.Column("needs_information", sa.Integer(), nullable=False),
            sa.Column("manual_only", sa.Integer(), nullable=False),
            sa.Column("failed", sa.Integer(), nullable=False),
            sa.Column("proposal_ready", sa.Integer(), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False),
            sa.Column("last_error", sa.String(length=128), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("event_cursor", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "task_id",
                "idempotency_key",
                name="uq_analysis_job_idempotency",
            ),
        )
        op.create_index("ix_analysis_jobs_task_id", "analysis_jobs", ["task_id"])
        op.create_index("ix_analysis_jobs_tenant_id", "analysis_jobs", ["tenant_id"])
        op.create_index("ix_analysis_jobs_requested_by", "analysis_jobs", ["requested_by"])
        op.create_index("ix_analysis_jobs_status", "analysis_jobs", ["status"])
        op.create_index(
            "ix_analysis_jobs_task_status",
            "analysis_jobs",
            ["task_id", "status"],
        )
    if "analysis_work_items" not in existing_tables:
        op.create_table(
            "analysis_work_items",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("job_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("difference_id", sa.Uuid(), nullable=False),
            sa.Column("difference_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("lease_owner", sa.String(length=128), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("result_id", sa.Uuid(), nullable=True),
            sa.Column("failure_code", sa.String(length=128), nullable=True),
            sa.Column("resolution_mode", sa.String(length=32), nullable=True),
            sa.Column("fallback", sa.JSON(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["difference_id"], ["difference_items.id"]),
            sa.ForeignKeyConstraint(["job_id"], ["analysis_jobs.id"]),
            sa.ForeignKeyConstraint(["result_id"], ["analysis_results.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "job_id",
                "difference_id",
                "difference_version",
                name="uq_analysis_work_item_difference",
            ),
        )
        for name, columns in {
            "ix_analysis_work_items_job_id": ["job_id"],
            "ix_analysis_work_items_tenant_id": ["tenant_id"],
            "ix_analysis_work_items_difference_id": ["difference_id"],
            "ix_analysis_work_items_status": ["status"],
            "ix_analysis_work_items_available_at": ["available_at"],
            "ix_analysis_work_items_lease_owner": ["lease_owner"],
            "ix_analysis_work_items_lease_expires_at": ["lease_expires_at"],
            "ix_analysis_work_items_result_id": ["result_id"],
            "ix_analysis_work_items_claim": [
                "job_id",
                "status",
                "available_at",
                "lease_expires_at",
            ],
        }.items():
            op.create_index(name, "analysis_work_items", columns)
    if context.is_offline_mode() or "analysis_job_id" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("workflow_stage_runs")
    }:
        op.add_column(
            "workflow_stage_runs",
            sa.Column("analysis_job_id", sa.Uuid(), nullable=True),
        )
        op.create_index(
            "ix_workflow_stage_runs_analysis_job_id",
            "workflow_stage_runs",
            ["analysis_job_id"],
        )
    if "proposal_batches" not in existing_tables:
        op.create_table(
            "proposal_batches",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("task_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("preview_hash", sa.String(length=64), nullable=False),
            sa.Column("created_by", sa.String(length=128), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "task_id",
                "idempotency_key",
                name="uq_proposal_batch_idempotency",
            ),
        )
        op.create_index("ix_proposal_batches_task_id", "proposal_batches", ["task_id"])
        op.create_index("ix_proposal_batches_tenant_id", "proposal_batches", ["tenant_id"])
        op.create_index("ix_proposal_batches_created_by", "proposal_batches", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_proposal_batches_created_by", table_name="proposal_batches")
    op.drop_index("ix_proposal_batches_tenant_id", table_name="proposal_batches")
    op.drop_index("ix_proposal_batches_task_id", table_name="proposal_batches")
    op.drop_table("proposal_batches")
    op.drop_index(
        "ix_workflow_stage_runs_analysis_job_id",
        table_name="workflow_stage_runs",
    )
    op.drop_column("workflow_stage_runs", "analysis_job_id")
    op.drop_index("ix_analysis_work_items_claim", table_name="analysis_work_items")
    op.drop_index("ix_analysis_work_items_result_id", table_name="analysis_work_items")
    op.drop_index("ix_analysis_work_items_lease_expires_at", table_name="analysis_work_items")
    op.drop_index("ix_analysis_work_items_lease_owner", table_name="analysis_work_items")
    op.drop_index("ix_analysis_work_items_available_at", table_name="analysis_work_items")
    op.drop_index("ix_analysis_work_items_status", table_name="analysis_work_items")
    op.drop_index("ix_analysis_work_items_difference_id", table_name="analysis_work_items")
    op.drop_index("ix_analysis_work_items_tenant_id", table_name="analysis_work_items")
    op.drop_index("ix_analysis_work_items_job_id", table_name="analysis_work_items")
    op.drop_table("analysis_work_items")
    op.drop_index("ix_analysis_jobs_task_status", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_status", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_requested_by", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_tenant_id", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_task_id", table_name="analysis_jobs")
    op.drop_table("analysis_jobs")

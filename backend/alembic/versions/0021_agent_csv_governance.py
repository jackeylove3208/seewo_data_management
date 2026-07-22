"""Persist approvals, conflict decisions, and Agent governance execution facts.

Revision ID: 0021_agent_csv_governance
Revises: 0020_agent_csv_analysis
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import context, op

revision: str = "0021_agent_csv_governance"
down_revision: str | None = "0020_agent_csv_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.is_offline_mode():
        bind = op.get_bind()
        existing = set(inspect(bind).get_table_names())
        if {
            "agent_approval_groups",
            "agent_clarifications",
            "agent_governance_plans",
            "agent_governance_operations",
        }.issubset(existing):
            return
    op.create_table(
        "agent_approval_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("group_key", sa.String(255), nullable=False),
        sa.Column("membership_hash", sa.String(64), nullable=False),
        sa.Column("finding_ids", sa.JSON(), nullable=False),
        sa.Column("issue_kind", sa.String(64), nullable=False),
        sa.Column("entity_kind", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("risk", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decision_reason", sa.String(1000), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "group_key", name="uq_agent_approval_group_key"),
        sa.CheckConstraint("risk = 'high'", name="ck_agent_approval_group_risk"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'stale')",
            name="ck_agent_approval_group_status",
        ),
    )
    op.create_index("ix_agent_approval_groups_run_id", "agent_approval_groups", ["run_id"])
    op.create_index("ix_agent_approval_groups_task_id", "agent_approval_groups", ["task_id"])
    op.create_index("ix_agent_approval_groups_tenant_id", "agent_approval_groups", ["tenant_id"])
    op.create_index("ix_agent_approval_groups_status", "agent_approval_groups", ["status"])

    op.create_table(
        "agent_clarifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("masked_candidates", sa.JSON(), nullable=False),
        sa.Column("allowed_outcomes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("original_text", sa.String(500), nullable=True),
        sa.Column("interpretation", sa.JSON(), nullable=True),
        sa.Column("interpreted_by", sa.String(128), nullable=True),
        sa.Column("confirmed_by", sa.String(128), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.ForeignKeyConstraint(["work_item_id"], ["agent_work_items.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["agent_model_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "work_item_id", name="uq_agent_clarification_work_item"),
        sa.CheckConstraint(
            "status IN ('pending', 'interpreted', 'confirmed', 'rejected')",
            name="ck_agent_clarification_status",
        ),
    )
    op.create_index("ix_agent_clarifications_run_id", "agent_clarifications", ["run_id"])
    op.create_index("ix_agent_clarifications_task_id", "agent_clarifications", ["task_id"])
    op.create_index("ix_agent_clarifications_tenant_id", "agent_clarifications", ["tenant_id"])
    op.create_index(
        "ix_agent_clarifications_work_item_id", "agent_clarifications", ["work_item_id"]
    )
    op.create_index("ix_agent_clarifications_status", "agent_clarifications", ["status"])

    op.create_table(
        "agent_governance_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("source_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("target_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("target_version", sa.String(128), nullable=False),
        sa.Column("finding_ids", sa.JSON(), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("compiled_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["snapshots.id"]),
        sa.ForeignKeyConstraint(["target_snapshot_id"], ["snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "content_hash", name="uq_agent_governance_plan_content"),
        sa.CheckConstraint(
            "status IN ('compiled', 'approved', 'executing', 'partial', 'succeeded', 'failed')",
            name="ck_agent_governance_plan_status",
        ),
    )
    op.create_index("ix_agent_governance_plans_run_id", "agent_governance_plans", ["run_id"])
    op.create_index("ix_agent_governance_plans_task_id", "agent_governance_plans", ["task_id"])
    op.create_index("ix_agent_governance_plans_tenant_id", "agent_governance_plans", ["tenant_id"])
    op.create_index("ix_agent_governance_plans_status", "agent_governance_plans", ["status"])

    op.create_table(
        "agent_governance_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("operation_type", sa.String(32), nullable=False),
        sa.Column("entity_kind", sa.String(32), nullable=False),
        sa.Column("target_source_identifier", sa.String(255), nullable=True),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("risk", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("actual_after", sa.JSON(), nullable=True),
        sa.Column("verification", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["agent_governance_plans.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.ForeignKeyConstraint(["finding_id"], ["agent_findings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "id", name="uq_agent_governance_operation"),
        sa.CheckConstraint(
            "operation_type IN ('create', 'update', 'delete', 'retain', 'skip')",
            name="ck_agent_governance_operation_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'blocked', "
            "'verification_failed')",
            name="ck_agent_governance_operation_status",
        ),
    )
    op.create_index(
        "ix_agent_governance_operations_plan_id", "agent_governance_operations", ["plan_id"]
    )
    op.create_index(
        "ix_agent_governance_operations_run_id", "agent_governance_operations", ["run_id"]
    )
    op.create_index(
        "ix_agent_governance_operations_task_id", "agent_governance_operations", ["task_id"]
    )
    op.create_index(
        "ix_agent_governance_operations_finding_id", "agent_governance_operations", ["finding_id"]
    )
    op.create_index(
        "ix_agent_governance_operations_operation_type",
        "agent_governance_operations",
        ["operation_type"],
    )
    op.create_index(
        "ix_agent_governance_operations_entity_kind", "agent_governance_operations", ["entity_kind"]
    )
    op.create_index(
        "ix_agent_governance_operations_status", "agent_governance_operations", ["status"]
    )


def downgrade() -> None:
    op.drop_table("agent_governance_operations")
    op.drop_table("agent_governance_plans")
    op.drop_table("agent_clarifications")
    op.drop_table("agent_approval_groups")

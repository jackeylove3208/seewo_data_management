"""Add durable new-Agent CSV analysis records.

Revision ID: 0020_agent_csv_analysis
Revises: 0019_agent_lease_fencing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import context, op

revision: str = "0020_agent_csv_analysis"
down_revision: str | None = "0019_agent_lease_fencing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = (
    "agent_connector_capabilities",
    "agent_input_records",
    "agent_input_marks",
    "agent_identity_postings",
    "agent_work_items",
    "agent_identity_evidence",
    "agent_identity_claims",
    "agent_model_batch_items",
    "agent_model_attempts",
    "agent_findings",
    "agent_finding_solutions",
    "agent_finding_dependencies",
)


def upgrade() -> None:
    bind = op.get_bind()
    if not context.is_offline_mode() and "agent_connector_capabilities" in set(
        inspect(bind).get_table_names()
    ):
        _create_immutability_triggers()
        return
    op.create_table(
        "agent_connector_capabilities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("source_role", sa.String(length=32), nullable=False),
        sa.Column("connector_kind", sa.String(length=32), nullable=False),
        sa.Column("capability_hash", sa.String(length=64), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_role IN ('authoritative', 'target')", name="ck_agent_capability_source_role"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["reconciliation_tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "source_role", "capability_hash", name="uq_agent_capability"),
    )
    op.create_index(
        op.f("ix_agent_connector_capabilities_run_id"),
        "agent_connector_capabilities",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_connector_capabilities_task_id"),
        "agent_connector_capabilities",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_connector_capabilities_tenant_id"),
        "agent_connector_capabilities",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "agent_model_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("entity_kind", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity_kind IN ('department', 'student', 'teacher')", name="ck_agent_batch_entity_kind"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'completed', 'blocked')", name="ck_agent_batch_status"
        ),
        sa.CheckConstraint(
            "item_count >= 1 AND item_count <= 50", name="ck_agent_batch_item_count"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["reconciliation_tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "input_hash", name="uq_agent_model_batch_input"),
    )
    op.create_index(
        op.f("ix_agent_model_batches_entity_kind"),
        "agent_model_batches",
        ["entity_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_model_batches_lease_owner"),
        "agent_model_batches",
        ["lease_owner"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_model_batches_run_id"), "agent_model_batches", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_model_batches_status"), "agent_model_batches", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_agent_model_batches_task_id"), "agent_model_batches", ["task_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_model_batches_tenant_id"), "agent_model_batches", ["tenant_id"], unique=False
    )
    op.create_table(
        "agent_input_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("source_role", sa.String(length=32), nullable=False),
        sa.Column("stable_locator", sa.String(length=512), nullable=False),
        sa.Column("stable_order", sa.Integer(), nullable=False),
        sa.Column("entity_kind", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("number", sa.String(length=255), nullable=True),
        sa.Column("class_name", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("raw_row_number", sa.Integer(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity_kind = 'student' OR class_name IS NULL", name="ck_agent_input_class"
        ),
        sa.CheckConstraint(
            "entity_kind IN ('department', 'student', 'teacher')", name="ck_agent_input_kind"
        ),
        sa.CheckConstraint(
            "source_role IN ('authoritative', 'target')", name="ck_agent_input_source_role"
        ),
        sa.CheckConstraint("stable_order >= 1", name="ck_agent_input_stable_order"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["reconciliation_tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "source_role", "stable_locator", name="uq_agent_input_locator"
        ),
        sa.UniqueConstraint("run_id", "source_role", "stable_order", name="uq_agent_input_order"),
    )
    op.create_index(
        op.f("ix_agent_input_records_entity_kind"),
        "agent_input_records",
        ["entity_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_input_records_run_id"), "agent_input_records", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_input_records_snapshot_id"),
        "agent_input_records",
        ["snapshot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_input_records_task_id"), "agent_input_records", ["task_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_input_records_tenant_id"), "agent_input_records", ["tenant_id"], unique=False
    )
    op.create_table(
        "agent_model_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("skill_name", sa.String(length=128), nullable=True),
        sa.Column("skill_version", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("gateway_request_id", sa.String(length=255), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("safe_error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="ck_agent_attempt_status"),
        sa.CheckConstraint(
            "attempt_number >= 1 AND attempt_number <= 4", name="ck_agent_attempt_number"
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["agent_model_batches.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "attempt_number", name="uq_agent_model_attempt"),
    )
    op.create_index(
        op.f("ix_agent_model_attempts_batch_id"), "agent_model_attempts", ["batch_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_model_attempts_status"), "agent_model_attempts", ["status"], unique=False
    )
    op.create_table(
        "agent_identity_postings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("input_record_id", sa.Uuid(), nullable=False),
        sa.Column("entity_kind", sa.String(length=32), nullable=False),
        sa.Column("key_kind", sa.String(length=16), nullable=False),
        sa.Column("normalized_value", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("key_kind IN ('number', 'phone', 'email')", name="ck_agent_posting_key"),
        sa.ForeignKeyConstraint(
            ["input_record_id"],
            ["agent_input_records.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["reconciliation_tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "input_record_id", "key_kind", name="uq_agent_identity_posting"
        ),
    )
    op.create_index(
        "ix_agent_identity_lookup",
        "agent_identity_postings",
        ["tenant_id", "snapshot_id", "entity_kind", "key_kind", "normalized_value"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_postings_entity_kind"),
        "agent_identity_postings",
        ["entity_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_postings_input_record_id"),
        "agent_identity_postings",
        ["input_record_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_postings_key_kind"),
        "agent_identity_postings",
        ["key_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_postings_run_id"),
        "agent_identity_postings",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_postings_snapshot_id"),
        "agent_identity_postings",
        ["snapshot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_postings_task_id"),
        "agent_identity_postings",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_postings_tenant_id"),
        "agent_identity_postings",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "agent_input_marks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("input_record_id", sa.Uuid(), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("affected_fields", sa.JSON(), nullable=False),
        sa.Column("inclusion_state", sa.String(length=32), nullable=False),
        sa.Column("report_disposition", sa.String(length=64), nullable=False),
        sa.Column("safe_evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "inclusion_state IN ('included', 'excluded', 'anomaly')",
            name="ck_agent_mark_inclusion_state",
        ),
        sa.ForeignKeyConstraint(
            ["input_record_id"],
            ["agent_input_records.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("input_record_id", "reason_code", name="uq_agent_input_mark"),
    )
    op.create_index(
        op.f("ix_agent_input_marks_inclusion_state"),
        "agent_input_marks",
        ["inclusion_state"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_input_marks_input_record_id"),
        "agent_input_marks",
        ["input_record_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_input_marks_reason_code"), "agent_input_marks", ["reason_code"], unique=False
    )
    op.create_table(
        "agent_work_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("source_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("target_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("subject_input_id", sa.Uuid(), nullable=False),
        sa.Column("entity_kind", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity_kind IN ('department', 'student', 'teacher')",
            name="ck_agent_work_item_entity_kind",
        ),
        sa.CheckConstraint(
            "kind IN ('resolved', 'identity_conflict', 'target_extra', "
            "'target_duplicate', 'target_missing', 'field_difference', 'correct')",
            name="ck_agent_work_item_kind",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'claimed', 'awaiting_clarification', 'analyzed', 'blocked')",
            name="ck_agent_work_item_state",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["snapshots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["subject_input_id"],
            ["agent_input_records.id"],
        ),
        sa.ForeignKeyConstraint(
            ["target_snapshot_id"],
            ["snapshots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["reconciliation_tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "idempotency_hash", name="uq_agent_work_item_replay"),
    )
    op.create_index(
        op.f("ix_agent_work_items_entity_kind"), "agent_work_items", ["entity_kind"], unique=False
    )
    op.create_index(op.f("ix_agent_work_items_kind"), "agent_work_items", ["kind"], unique=False)
    op.create_index(
        op.f("ix_agent_work_items_run_id"), "agent_work_items", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_work_items_source_snapshot_id"),
        "agent_work_items",
        ["source_snapshot_id"],
        unique=False,
    )
    op.create_index(op.f("ix_agent_work_items_state"), "agent_work_items", ["state"], unique=False)
    op.create_index(
        op.f("ix_agent_work_items_subject_input_id"),
        "agent_work_items",
        ["subject_input_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_work_items_target_snapshot_id"),
        "agent_work_items",
        ["target_snapshot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_work_items_task_id"), "agent_work_items", ["task_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_work_items_tenant_id"), "agent_work_items", ["tenant_id"], unique=False
    )
    op.create_table(
        "agent_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("category_zh", sa.String(length=255), nullable=False),
        sa.Column("analysis_zh", sa.String(length=8000), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["agent_model_batches.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["reconciliation_tasks.id"],
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["agent_work_items.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_item_id", name="uq_agent_finding_work_item"),
    )
    op.create_index(
        op.f("ix_agent_findings_batch_id"), "agent_findings", ["batch_id"], unique=False
    )
    op.create_index(op.f("ix_agent_findings_kind"), "agent_findings", ["kind"], unique=False)
    op.create_index(op.f("ix_agent_findings_run_id"), "agent_findings", ["run_id"], unique=False)
    op.create_index(op.f("ix_agent_findings_task_id"), "agent_findings", ["task_id"], unique=False)
    op.create_index(
        op.f("ix_agent_findings_work_item_id"), "agent_findings", ["work_item_id"], unique=False
    )
    op.create_table(
        "agent_identity_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("source_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("target_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("authority_input_id", sa.Uuid(), nullable=False),
        sa.Column("target_input_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["authority_input_id"],
            ["agent_input_records.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["snapshots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["target_input_id"],
            ["agent_input_records.id"],
        ),
        sa.ForeignKeyConstraint(
            ["target_snapshot_id"],
            ["snapshots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["reconciliation_tasks.id"],
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["agent_work_items.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "authority_input_id", name="uq_agent_claim_authority"),
        sa.UniqueConstraint("run_id", "target_input_id", name="uq_agent_claim_target"),
        sa.UniqueConstraint("work_item_id", name="uq_agent_claim_work_item"),
    )
    op.create_index(
        op.f("ix_agent_identity_claims_authority_input_id"),
        "agent_identity_claims",
        ["authority_input_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_claims_run_id"), "agent_identity_claims", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_identity_claims_source_snapshot_id"),
        "agent_identity_claims",
        ["source_snapshot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_claims_target_input_id"),
        "agent_identity_claims",
        ["target_input_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_claims_target_snapshot_id"),
        "agent_identity_claims",
        ["target_snapshot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_claims_task_id"), "agent_identity_claims", ["task_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_identity_claims_work_item_id"),
        "agent_identity_claims",
        ["work_item_id"],
        unique=False,
    )
    op.create_table(
        "agent_identity_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("key_kind", sa.String(length=16), nullable=False),
        sa.Column("normalized_value", sa.String(length=512), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["posting_id"],
            ["agent_identity_postings.id"],
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["agent_work_items.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_item_id", "posting_id", name="uq_agent_identity_evidence"),
    )
    op.create_index(
        op.f("ix_agent_identity_evidence_posting_id"),
        "agent_identity_evidence",
        ["posting_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_identity_evidence_work_item_id"),
        "agent_identity_evidence",
        ["work_item_id"],
        unique=False,
    )
    op.create_table(
        "agent_model_batch_items",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["agent_model_batches.id"],
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["agent_work_items.id"],
        ),
        sa.PrimaryKeyConstraint("batch_id", "work_item_id"),
        sa.UniqueConstraint("batch_id", "ordinal", name="uq_agent_batch_item_order"),
    )
    op.create_table(
        "agent_finding_dependencies",
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("depends_on_finding_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["depends_on_finding_id"],
            ["agent_findings.id"],
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["agent_findings.id"],
        ),
        sa.PrimaryKeyConstraint("finding_id", "depends_on_finding_id"),
    )
    op.create_table(
        "agent_finding_solutions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("risk", sa.String(length=32), nullable=False),
        sa.Column("solution_zh", sa.String(length=4000), nullable=False),
        sa.Column("recommended", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation IN ('create', 'update', 'delete', 'retain', 'skip')",
            name="ck_agent_solution_operation",
        ),
        sa.CheckConstraint("risk IN ('low', 'medium', 'high')", name="ck_agent_solution_risk"),
        sa.CheckConstraint("ordinal >= 1 AND ordinal <= 3", name="ck_agent_solution_ordinal"),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["agent_findings.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("finding_id", "ordinal", name="uq_agent_finding_solution"),
    )
    op.create_index(
        op.f("ix_agent_finding_solutions_finding_id"),
        "agent_finding_solutions",
        ["finding_id"],
        unique=False,
    )

    _create_immutability_triggers()


def _create_immutability_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_agent_analysis_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'new-Agent analysis records are append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table in _IMMUTABLE_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS reject_{table}_mutation ON {table}")
            op.execute(
                f"CREATE TRIGGER reject_{table}_mutation "
                f"BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_agent_analysis_mutation()"
            )
    elif dialect == "sqlite":
        for table in _IMMUTABLE_TABLES:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS reject_{table}_{action}")
                op.execute(
                    f"CREATE TRIGGER reject_{table}_{action} "
                    f"BEFORE {action.upper()} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, "
                    "'new-Agent analysis records are append-only'); END"
                )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table in _IMMUTABLE_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS reject_{table}_mutation ON {table}")
        op.execute("DROP FUNCTION IF EXISTS reject_agent_analysis_mutation()")
    elif dialect == "sqlite":
        for table in _IMMUTABLE_TABLES:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS reject_{table}_{action}")

    for table in (
        "agent_finding_solutions",
        "agent_finding_dependencies",
        "agent_model_batch_items",
        "agent_identity_evidence",
        "agent_identity_claims",
        "agent_findings",
        "agent_work_items",
        "agent_input_marks",
        "agent_identity_postings",
        "agent_model_attempts",
        "agent_input_records",
        "agent_model_batches",
        "agent_connector_capabilities",
    ):
        op.drop_table(table)

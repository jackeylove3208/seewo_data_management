"""Create durable entity rematching jobs and candidate history.

Revision ID: 0013_entity_rematch_jobs
Revises: 0012_snapshot_entity_embeddings
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0013_entity_rematch_jobs"
down_revision: str | None = "0012_snapshot_entity_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.is_offline_mode():
        inspector = sa.inspect(op.get_bind())
        tables = set(inspector.get_table_names())
        mapping_columns = {
            column["name"] for column in inspector.get_columns("entity_mappings")
        }
        rematch_tables = {
            "entity_rematch_jobs",
            "entity_rematch_work_items",
            "entity_rematch_candidate_edges",
        }
        if rematch_tables <= tables and "supersedes_mapping_id" in mapping_columns:
            return
    with op.batch_alter_table("entity_mappings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "supersedes_mapping_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "entity_mappings.id",
                    name="fk_entity_mappings_supersedes_mapping_id",
                ),
                nullable=True,
            )
        )
    op.create_index(
        "ix_entity_mappings_supersedes_mapping_id",
        "entity_mappings",
        ["supersedes_mapping_id"],
    )
    op.create_table(
        "entity_rematch_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("source_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("target_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_recovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("no_match", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manual_review", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conflict", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["snapshots.id"]),
        sa.ForeignKeyConstraint(["target_snapshot_id"], ["snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            "idempotency_key",
            name="uq_entity_rematch_job_idempotency",
        ),
    )
    for name, columns in {
        "ix_entity_rematch_jobs_task_id": ["task_id"],
        "ix_entity_rematch_jobs_tenant_id": ["tenant_id"],
        "ix_entity_rematch_jobs_source_snapshot_id": ["source_snapshot_id"],
        "ix_entity_rematch_jobs_target_snapshot_id": ["target_snapshot_id"],
        "ix_entity_rematch_jobs_status": ["status"],
        "ix_entity_rematch_jobs_task_status": ["tenant_id", "task_id", "status"],
    }.items():
        op.create_index(name, "entity_rematch_jobs", columns)

    op.create_table(
        "entity_rematch_work_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("focal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("focal_role", sa.String(32), nullable=False),
        sa.Column("candidate_set_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_status", sa.String(32), nullable=True),
        sa.Column("outcome", sa.JSON(), nullable=True),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("reused_from_item_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["entity_rematch_jobs.id"]),
        sa.ForeignKeyConstraint(["focal_entity_id"], ["canonical_entities.id"]),
        sa.ForeignKeyConstraint(["reused_from_item_id"], ["entity_rematch_work_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "focal_role",
            "focal_entity_id",
            name="uq_entity_rematch_work_item_focal",
        ),
    )
    for name, columns in {
        "ix_entity_rematch_work_items_job_id": ["job_id"],
        "ix_entity_rematch_work_items_tenant_id": ["tenant_id"],
        "ix_entity_rematch_work_items_entity_type": ["entity_type"],
        "ix_entity_rematch_work_items_focal_entity_id": ["focal_entity_id"],
        "ix_entity_rematch_work_items_candidate_set_hash": ["candidate_set_hash"],
        "ix_entity_rematch_work_items_status": ["status"],
        "ix_entity_rematch_work_items_available_at": ["available_at"],
        "ix_entity_rematch_work_items_lease_owner": ["lease_owner"],
        "ix_entity_rematch_work_items_lease_expires_at": ["lease_expires_at"],
        "ix_entity_rematch_work_items_reused_from_item_id": ["reused_from_item_id"],
        "ix_entity_rematch_work_items_claim": [
            "tenant_id",
            "job_id",
            "status",
            "available_at",
            "lease_expires_at",
        ],
        "ix_entity_rematch_outcome_reuse": [
            "tenant_id",
            "entity_type",
            "focal_role",
            "focal_entity_id",
            "candidate_set_hash",
            "policy_version",
            "outcome_status",
        ],
    }.items():
        op.create_index(name, "entity_rematch_work_items", columns)

    op.create_table(
        "entity_rematch_candidate_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("focal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("focal_role", sa.String(32), nullable=False),
        sa.Column("candidate_entity_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_role", sa.String(32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("vector_score", sa.Float(), nullable=True),
        sa.Column("lexical_score", sa.Float(), nullable=True),
        sa.Column("representation_version", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["entity_rematch_jobs.id"]),
        sa.ForeignKeyConstraint(["work_item_id"], ["entity_rematch_work_items.id"]),
        sa.ForeignKeyConstraint(["focal_entity_id"], ["canonical_entities.id"]),
        sa.ForeignKeyConstraint(["candidate_entity_id"], ["canonical_entities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_item_id",
            "candidate_role",
            "candidate_entity_id",
            name="uq_entity_rematch_candidate_edge",
        ),
    )
    for name, columns in {
        "ix_entity_rematch_candidate_edges_job_id": ["job_id"],
        "ix_entity_rematch_candidate_edges_work_item_id": ["work_item_id"],
        "ix_entity_rematch_candidate_edges_tenant_id": ["tenant_id"],
        "ix_entity_rematch_candidate_edges_candidate_entity_id": ["candidate_entity_id"],
        "ix_entity_rematch_candidate_rank": ["tenant_id", "work_item_id", "rank"],
    }.items():
        op.create_index(name, "entity_rematch_candidate_edges", columns)


def downgrade() -> None:
    op.drop_table("entity_rematch_candidate_edges")
    op.drop_table("entity_rematch_work_items")
    op.drop_table("entity_rematch_jobs")
    op.drop_index("ix_entity_mappings_supersedes_mapping_id", table_name="entity_mappings")
    with op.batch_alter_table("entity_mappings") as batch_op:
        batch_op.drop_constraint(
            "fk_entity_mappings_supersedes_mapping_id", type_="foreignkey"
        )
        batch_op.drop_column("supersedes_mapping_id")

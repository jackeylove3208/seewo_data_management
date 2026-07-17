"""Create immutable reconciliation differences.

Revision ID: 0004_differences
Revises: 0003_target_embeddings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0004_differences"
down_revision: str | None = "0003_target_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    evidence_type = JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()
    op.create_table(
        "difference_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("source_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("target_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("mapping_id", sa.Uuid(), nullable=True),
        sa.Column("source_entity_id", sa.Uuid(), nullable=True),
        sa.Column("target_entity_id", sa.Uuid(), nullable=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("difference_type", sa.String(32), nullable=False),
        sa.Column("resolution_status", sa.String(32), nullable=False),
        sa.Column("analysis_status", sa.String(32), nullable=False),
        sa.Column("risk", sa.String(32), nullable=True),
        sa.Column("proposed_action", sa.String(32), nullable=False),
        sa.Column("evidence", evidence_type, nullable=False),
        sa.Column("comparison_rule_version", sa.String(64), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["mapping_id"], ["entity_mappings.id"]),
        sa.ForeignKeyConstraint(["source_entity_id"], ["canonical_entities.id"]),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["snapshots.id"]),
        sa.ForeignKeyConstraint(["target_entity_id"], ["canonical_entities.id"]),
        sa.ForeignKeyConstraint(["target_snapshot_id"], ["snapshots.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["reconciliation_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "source_snapshot_id",
            "target_snapshot_id",
            "entity_type",
            "evidence_hash",
            name="uq_difference_evidence",
        ),
    )
    for name, columns in (
        ("ix_difference_task", ["task_id"]),
        ("ix_difference_tenant", ["tenant_id"]),
        ("ix_difference_source_snapshot", ["source_snapshot_id"]),
        ("ix_difference_target_snapshot", ["target_snapshot_id"]),
        ("ix_difference_mapping", ["mapping_id"]),
        ("ix_difference_source_entity", ["source_entity_id"]),
        ("ix_difference_target_entity", ["target_entity_id"]),
        ("ix_difference_entity_type", ["entity_type"]),
        ("ix_difference_type", ["difference_type"]),
        ("ix_difference_resolution_status", ["resolution_status"]),
        ("ix_difference_analysis_status", ["analysis_status"]),
        ("ix_difference_risk", ["risk"]),
    ):
        op.create_index(name, "difference_items", columns)
    op.create_index(
        "ix_difference_task_filters",
        "difference_items",
        [
            "task_id",
            "entity_type",
            "difference_type",
            "resolution_status",
            "created_at",
            "id",
        ],
    )


def downgrade() -> None:
    op.drop_table("difference_items")

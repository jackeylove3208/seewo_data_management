"""Generalize target embeddings to role-aware snapshot embeddings.

Revision ID: 0012_snapshot_entity_embeddings
Revises: 0011_analysis_work_item_created_at
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0012_snapshot_entity_embeddings"
down_revision: str | None = "0011_analysis_work_item_created_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if _table_exists("snapshot_entity_embeddings") and not _table_exists(
        "target_entity_embeddings"
    ):
        return
    op.rename_table("target_entity_embeddings", "snapshot_entity_embeddings")
    op.add_column(
        "snapshot_entity_embeddings",
        sa.Column("source_role", sa.String(32), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE snapshot_entity_embeddings SET source_role = 'target' "
            "WHERE source_role IS NULL"
        )
    )
    with op.batch_alter_table("snapshot_entity_embeddings") as batch_op:
        batch_op.drop_constraint("uq_target_embedding_version", type_="unique")
        batch_op.create_unique_constraint(
            "uq_snapshot_embedding_version",
            [
                "tenant_id",
                "snapshot_id",
                "source_role",
                "entity_type",
                "entity_id",
                "provider",
                "model",
                "representation_version",
            ],
        )
        batch_op.alter_column(
            "source_role",
            existing_type=sa.String(32),
            nullable=False,
        )
    op.create_index(
        "ix_snapshot_embedding_role",
        "snapshot_entity_embeddings",
        ["source_role"],
    )
    op.create_index(
        "ix_snapshot_embedding_partition",
        "snapshot_entity_embeddings",
        [
            "tenant_id",
            "snapshot_id",
            "source_role",
            "entity_type",
            "campus_id",
            "grade",
            "parent_mapping_id",
        ],
    )


def downgrade() -> None:
    if not _table_exists("snapshot_entity_embeddings"):
        return
    op.execute(
        sa.text(
            "DELETE FROM snapshot_entity_embeddings "
            "WHERE source_role <> 'target'"
        )
    )
    op.drop_index("ix_snapshot_embedding_partition", table_name="snapshot_entity_embeddings")
    op.drop_index("ix_snapshot_embedding_role", table_name="snapshot_entity_embeddings")
    with op.batch_alter_table("snapshot_entity_embeddings") as batch_op:
        batch_op.drop_constraint("uq_snapshot_embedding_version", type_="unique")
        batch_op.create_unique_constraint(
            "uq_target_embedding_version",
            ["entity_id", "provider", "model", "representation_version"],
        )
        batch_op.drop_column("source_role")
    op.rename_table("snapshot_entity_embeddings", "target_entity_embeddings")


def _table_exists(name: str) -> bool:
    if context.is_offline_mode():
        return False
    return name in sa.inspect(op.get_bind()).get_table_names()

"""Create target embedding cache.

Revision ID: 0003_target_embeddings
Revises: 0002_entity_resolution
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0003_target_embeddings"
down_revision: str | None = "0002_entity_resolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    embedding_type = Vector(1536) if dialect == "postgresql" else sa.JSON()
    block_key_type = JSONB() if dialect == "postgresql" else sa.JSON()
    if dialect == "postgresql":
        op.execute(
            """
            DO $$
            DECLARE installed_version integer[];
            BEGIN
                SELECT string_to_array(extversion, '.')::integer[]
                  INTO installed_version
                  FROM pg_extension
                 WHERE extname = 'vector';
                IF installed_version IS NULL OR installed_version < ARRAY[0, 8, 0] THEN
                    RAISE EXCEPTION 'pgvector 0.8.0 or newer is required';
                END IF;
            END $$;
            """
        )
    op.create_table(
        "target_entity_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("campus_id", sa.String(255), nullable=True),
        sa.Column("grade", sa.String(64), nullable=True),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("normalized_values", sa.JSON(), nullable=False),
        sa.Column("parent_mapping_id", sa.Uuid(), nullable=True),
        sa.Column("block_key", block_key_type, nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("representation_version", sa.String(64), nullable=False),
        sa.Column("representation", sa.Text(), nullable=False),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["canonical_entities.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_id",
            "provider",
            "model",
            "representation_version",
            name="uq_target_embedding_version",
        ),
    )
    for name, columns in (
        ("ix_target_embedding_entity", ["entity_id"]),
        ("ix_target_embedding_snapshot", ["snapshot_id"]),
        ("ix_target_embedding_tenant", ["tenant_id"]),
        ("ix_target_embedding_type", ["entity_type"]),
    ):
        op.create_index(name, "target_entity_embeddings", columns)
    op.create_index(
        "ix_target_embedding_partition",
        "target_entity_embeddings",
        [
            "snapshot_id",
            "tenant_id",
            "entity_type",
            "campus_id",
            "grade",
            "parent_mapping_id",
        ],
    )
    if dialect == "postgresql":
        op.create_index(
            "ix_target_embedding_hnsw",
            "target_entity_embeddings",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )


def downgrade() -> None:
    op.drop_table("target_entity_embeddings")

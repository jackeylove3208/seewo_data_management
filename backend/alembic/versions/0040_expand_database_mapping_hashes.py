"""Allow prefixed SHA-256 hashes in database mapping caches.

Revision ID: 0040_mapping_hash_widths
Revises: 0039_api_connectors
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0040_mapping_hash_widths"
down_revision: str | Sequence[str] | None = "0039_api_connectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HASH_COLUMNS = (
    "authoritative_schema_fingerprint",
    "target_schema_fingerprint",
    "content_hash",
)


def upgrade() -> None:
    with op.batch_alter_table("agent_database_schema_mappings") as batch_op:
        for column_name in _HASH_COLUMNS:
            batch_op.alter_column(
                column_name,
                existing_type=sa.String(length=64),
                type_=sa.String(length=71),
                existing_nullable=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("agent_database_schema_mappings") as batch_op:
        for column_name in _HASH_COLUMNS:
            batch_op.alter_column(
                column_name,
                existing_type=sa.String(length=71),
                type_=sa.String(length=64),
                existing_nullable=False,
            )

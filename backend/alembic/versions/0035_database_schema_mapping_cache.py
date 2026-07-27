"""Cache validated database schema mappings across Agent tasks.

Revision ID: 0035_db_schema_mapping_cache
Revises: 0034_agent_run_contract_versions
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0035_db_schema_mapping_cache"
down_revision: str | Sequence[str] | None = "0034_agent_run_contract_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.is_offline_mode():
        tables = set(sa.inspect(op.get_bind()).get_table_names())
        if "agent_database_schema_mappings" in tables:
            return
    op.create_table(
        "agent_database_schema_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("authoritative_connector_id", sa.String(length=128), nullable=False),
        sa.Column("target_connector_id", sa.String(length=128), nullable=False),
        sa.Column(
            "authoritative_schema_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("target_schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("ingestion_contract_version", sa.String(length=64), nullable=False),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column("skill_version", sa.String(length=64), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "authoritative_connector_id",
            "target_connector_id",
            "authoritative_schema_fingerprint",
            "target_schema_fingerprint",
            "ingestion_contract_version",
            "skill_name",
            "skill_version",
            name="uq_agent_database_schema_mapping_cache",
        ),
    )
    op.create_index(
        "ix_agent_database_schema_mappings_tenant_id",
        "agent_database_schema_mappings",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    if not context.is_offline_mode():
        tables = set(sa.inspect(op.get_bind()).get_table_names())
        if "agent_database_schema_mappings" not in tables:
            return
    op.drop_index(
        "ix_agent_database_schema_mappings_tenant_id",
        table_name="agent_database_schema_mappings",
    )
    op.drop_table("agent_database_schema_mappings")

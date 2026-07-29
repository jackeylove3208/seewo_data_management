"""Freeze task-bound API connection inputs.

Revision ID: 0040_freeze_api_source
Revises: 0039_api_connectors
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0040_freeze_api_source"
down_revision: str | Sequence[str] | None = "0039_api_connectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing: set[str] = set()
    if not context.is_offline_mode():
        existing = {
            column["name"]
            for column in sa.inspect(op.get_bind()).get_columns(
                "api_authority_sources"
            )
        }
        if {
            "frozen_public_configuration",
            "frozen_secret_ref",
        } <= existing:
            return

    with op.batch_alter_table("api_authority_sources") as batch:
        if "frozen_public_configuration" not in existing:
            batch.add_column(
                sa.Column("frozen_public_configuration", sa.JSON(), nullable=True)
            )
        if "frozen_secret_ref" not in existing:
            batch.add_column(sa.Column("frozen_secret_ref", sa.String(128), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE api_authority_sources
            SET frozen_public_configuration = (
                    SELECT public_configuration
                    FROM api_connections
                    WHERE api_connections.id = api_authority_sources.connection_id
                ),
                frozen_secret_ref = (
                    SELECT secret_ref
                    FROM api_connections
                    WHERE api_connections.id = api_authority_sources.connection_id
                )
            WHERE frozen_public_configuration IS NULL
               OR frozen_secret_ref IS NULL
            """
        )
    )
    with op.batch_alter_table("api_authority_sources") as batch:
        batch.alter_column(
            "frozen_public_configuration",
            existing_type=sa.JSON(),
            nullable=False,
        )
        batch.alter_column(
            "frozen_secret_ref",
            existing_type=sa.String(128),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("api_authority_sources") as batch:
        batch.drop_column("frozen_secret_ref")
        batch.drop_column("frozen_public_configuration")

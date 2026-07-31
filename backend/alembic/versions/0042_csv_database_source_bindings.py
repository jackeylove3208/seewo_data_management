"""Allow CSV authorities to use frozen database targets.

Revision ID: 0042_csv_database_bindings
Revises: 0041_task_scoped_api_connections
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0042_csv_database_bindings"
down_revision: str | Sequence[str] | None = "0041_task_scoped_api_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_source_bindings") as batch_op:
        batch_op.drop_constraint(
            "ck_agent_source_binding_connector_kind",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_agent_source_binding_connector_kind",
            "connector_kind IN ('api', 'database', 'csv')",
        )


def downgrade() -> None:
    csv_binding_count = op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM agent_source_bindings "
            "WHERE connector_kind = 'csv'"
        )
    )
    if csv_binding_count:
        raise RuntimeError(
            "Cannot downgrade while CSV source bindings exist; "
            "archive or migrate those tasks first"
        )
    with op.batch_alter_table("agent_source_bindings") as batch_op:
        batch_op.drop_constraint(
            "ck_agent_source_binding_connector_kind",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_agent_source_binding_connector_kind",
            "connector_kind IN ('api', 'database')",
        )

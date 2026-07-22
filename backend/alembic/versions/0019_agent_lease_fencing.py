"""Add opaque fencing token to Agent run leases.

Revision ID: 0019_agent_lease_fencing
Revises: 0018_agent_runtime_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import context, op

revision: str = "0019_agent_lease_fencing"
down_revision: str | None = "0018_agent_runtime_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = None if context.is_offline_mode() else inspect(op.get_bind())
    columns = (
        set()
        if inspector is None
        else {column["name"] for column in inspector.get_columns("agent_runs")}
    )
    if "lease_token" not in columns:
        op.add_column("agent_runs", sa.Column("lease_token", sa.Uuid(), nullable=True))


def downgrade() -> None:
    inspector = None if context.is_offline_mode() else inspect(op.get_bind())
    columns = (
        {"lease_token"}
        if inspector is None
        else {column["name"] for column in inspector.get_columns("agent_runs")}
    )
    if "lease_token" in columns:
        op.drop_column("agent_runs", "lease_token")

"""Freeze ingestion and execution contracts on every Agent run.

Revision ID: 0034_agent_run_contract_versions
Revises: 0033_conversation_reset
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0034_agent_run_contract_versions"
down_revision: str | Sequence[str] | None = "0033_conversation_reset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INGESTION_DEFAULT = "model-mediated-ingestion-v1"
_EXECUTION_DEFAULT = "model-mediated-execution-v1"


def upgrade() -> None:
    if context.is_offline_mode():
        _add_columns()
        return
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_runs")
    }
    if "ingestion_contract_version" not in columns:
        op.add_column(
            "agent_runs",
            sa.Column(
                "ingestion_contract_version",
                sa.String(length=64),
                nullable=False,
                server_default=_INGESTION_DEFAULT,
            ),
        )
    if "execution_contract_version" not in columns:
        op.add_column(
            "agent_runs",
            sa.Column(
                "execution_contract_version",
                sa.String(length=64),
                nullable=False,
                server_default=_EXECUTION_DEFAULT,
            ),
        )


def downgrade() -> None:
    if context.is_offline_mode():
        op.drop_column("agent_runs", "execution_contract_version")
        op.drop_column("agent_runs", "ingestion_contract_version")
        return
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_runs")
    }
    if "execution_contract_version" in columns:
        op.drop_column("agent_runs", "execution_contract_version")
    if "ingestion_contract_version" in columns:
        op.drop_column("agent_runs", "ingestion_contract_version")


def _add_columns() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "ingestion_contract_version",
            sa.String(length=64),
            nullable=False,
            server_default=_INGESTION_DEFAULT,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "execution_contract_version",
            sa.String(length=64),
            nullable=False,
            server_default=_EXECUTION_DEFAULT,
        ),
    )

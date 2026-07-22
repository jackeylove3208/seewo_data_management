"""Merge concurrent Agent governance and CSV/reporting heads.

Revision ID: 0023_merge_agent_governance
Revises: 0021_agent_csv_governance, 0022_merge_agent_csv_reporting
"""

from collections.abc import Sequence

revision: str = "0023_merge_agent_governance"
down_revision: tuple[str, str] = (
    "0021_agent_csv_governance",
    "0022_merge_agent_csv_reporting",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""Merge concurrent CSV analysis and reporting migration heads.

Revision ID: 0022_merge_agent_csv_reporting
Revises: 0020_agent_reporting_history, 0021_authority_invalid_work
"""

from collections.abc import Sequence

revision: str = "0022_merge_agent_csv_reporting"
down_revision: tuple[str, str] = (
    "0020_agent_reporting_history",
    "0021_authority_invalid_work",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

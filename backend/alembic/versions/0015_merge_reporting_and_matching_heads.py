"""Merge governance reporting and matching migration heads.

Revision ID: 0015_merge_reporting_and_matching_heads
Revises: 0012_reporting_historical_restore, 0014_matching_quality_results
"""

from collections.abc import Sequence

revision: str = "0015_merge_reporting_and_matching_heads"
down_revision: tuple[str, str] = (
    "0012_reporting_historical_restore",
    "0014_matching_quality_results",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Unify independently developed migration branches without schema changes."""


def downgrade() -> None:
    """Restore the two parent heads without schema changes."""

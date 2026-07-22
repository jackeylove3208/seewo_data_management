"""Allow read-only invalid-authority Agent findings.

Revision ID: 0021_authority_invalid_work
Revises: 0020_agent_csv_analysis
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_authority_invalid_work"
down_revision: str | None = "0020_agent_csv_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = (
    "kind IN ('resolved', 'identity_conflict', 'target_extra', 'target_duplicate', "
    "'target_missing', 'field_difference', 'authority_invalid', 'correct')"
)
_OLD_CHECK = (
    "kind IN ('resolved', 'identity_conflict', 'target_extra', 'target_duplicate', "
    "'target_missing', 'field_difference', 'correct')"
)


def upgrade() -> None:
    with op.batch_alter_table("agent_work_items") as batch:
        batch.drop_constraint("ck_agent_work_item_kind", type_="check")
        batch.create_check_constraint("ck_agent_work_item_kind", _CHECK)


def downgrade() -> None:
    with op.batch_alter_table("agent_work_items") as batch:
        batch.drop_constraint("ck_agent_work_item_kind", type_="check")
        batch.create_check_constraint("ck_agent_work_item_kind", _OLD_CHECK)

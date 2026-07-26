"""Allow medium-risk Agent approval groups.

Revision ID: 0031_agent_reviewable_risk
Revises: 0030_agent_checkpoint_hash
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031_agent_reviewable_risk"
down_revision: str | Sequence[str] | None = "0030_agent_checkpoint_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_approval_groups") as batch_op:
        batch_op.drop_constraint("ck_agent_approval_group_risk", type_="check")
        batch_op.create_check_constraint(
            "ck_agent_approval_group_risk",
            "risk IN ('medium', 'high')",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE agent_approval_groups SET risk = 'high' WHERE risk = 'medium'"
    )
    with op.batch_alter_table("agent_approval_groups") as batch_op:
        batch_op.drop_constraint("ck_agent_approval_group_risk", type_="check")
        batch_op.create_check_constraint(
            "ck_agent_approval_group_risk",
            "risk = 'high'",
        )

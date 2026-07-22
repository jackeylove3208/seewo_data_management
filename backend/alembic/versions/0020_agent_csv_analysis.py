"""Add durable records for new-Agent CSV analysis.

Revision ID: 0020_agent_csv_analysis
Revises: 0019_agent_lease_fencing
"""

from collections.abc import Sequence

from alembic import context, op
from app.models.agent_analysis import (
    AgentConnectorCapabilityRecord,
    AgentFindingDependencyRecord,
    AgentFindingRecord,
    AgentFindingSolutionRecord,
    AgentIdentityClaimRecord,
    AgentIdentityEvidenceRecord,
    AgentIdentityPostingRecord,
    AgentInputMarkRecord,
    AgentInputRecord,
    AgentModelAttemptRecord,
    AgentModelBatchItemRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)

revision: str = "0020_agent_csv_analysis"
down_revision: str | None = "0019_agent_lease_fencing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = (
    AgentConnectorCapabilityRecord.__table__,
    AgentModelBatchRecord.__table__,
    AgentInputRecord.__table__,
    AgentModelAttemptRecord.__table__,
    AgentIdentityPostingRecord.__table__,
    AgentInputMarkRecord.__table__,
    AgentWorkItemRecord.__table__,
    AgentFindingRecord.__table__,
    AgentIdentityClaimRecord.__table__,
    AgentIdentityEvidenceRecord.__table__,
    AgentModelBatchItemRecord.__table__,
    AgentFindingDependencyRecord.__table__,
    AgentFindingSolutionRecord.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    checkfirst = not context.is_offline_mode()
    for table in _TABLES:
        table.create(bind, checkfirst=checkfirst)


def downgrade() -> None:
    bind = op.get_bind()
    checkfirst = not context.is_offline_mode()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=checkfirst)

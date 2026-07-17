from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ImmutableProposalError(ValueError):
    pass


class GovernanceProposalRecord(Base):
    __tablename__ = "governance_proposals"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    difference_id: Mapped[UUID] = mapped_column(ForeignKey("difference_items.id"), index=True)
    difference_version: Mapped[int] = mapped_column(Integer)
    analysis_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_results.id"), index=True)
    analysis_version: Mapped[str] = mapped_column(String(64))
    proposal_version: Mapped[int] = mapped_column(Integer)
    proposal_source: Mapped[str] = mapped_column(String(32))
    operation_type: Mapped[str] = mapped_column(String(32))
    target_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("canonical_entities.id"), nullable=True
    )
    changes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    rationale: Mapped[str] = mapped_column(String(2000))
    evidence_refs: Mapped[list[str]] = mapped_column(JSON)
    risk: Mapped[str] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    supersedes_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("governance_proposals.id"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "difference_id",
            "difference_version",
            "proposal_version",
            name="uq_governance_proposal_version",
        ),
    )


def _reject_mutation(_mapper: object, _connection: object, target: object) -> None:
    raise ImmutableProposalError(f"{type(target).__name__} is immutable")


event.listen(GovernanceProposalRecord, "before_update", _reject_mutation)
event.listen(GovernanceProposalRecord, "before_delete", _reject_mutation)

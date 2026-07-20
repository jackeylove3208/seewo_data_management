from datetime import UTC
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proposals import GovernanceProposalRecord, ImmutableProposalError
from app.schemas.proposals import (
    GovernanceProposal,
    GovernanceProposalPreview,
    ProposalStatus,
)

__all__ = ["ImmutableProposalError", "ProposalRepository"]


class ProposalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, proposal_id: UUID) -> GovernanceProposal | None:
        record = await self.session.get(GovernanceProposalRecord, proposal_id)
        return self._proposal(record) if record is not None else None

    async def get_current(
        self, difference_id: UUID, difference_version: int
    ) -> GovernanceProposal | None:
        record = await self.session.scalar(
            select(GovernanceProposalRecord)
            .where(
                GovernanceProposalRecord.difference_id == difference_id,
                GovernanceProposalRecord.difference_version == difference_version,
            )
            .order_by(GovernanceProposalRecord.proposal_version.desc())
        )
        return self._proposal(record) if record is not None else None

    async def list_for_difference(self, difference_id: UUID) -> tuple[GovernanceProposal, ...]:
        records = await self.session.scalars(
            select(GovernanceProposalRecord)
            .where(GovernanceProposalRecord.difference_id == difference_id)
            .order_by(GovernanceProposalRecord.proposal_version.desc())
        )
        return tuple(self._proposal(record) for record in records)

    async def create(
        self,
        *,
        task_id: UUID,
        tenant_id: str,
        analysis_id: UUID,
        analysis_version: str,
        preview: GovernanceProposalPreview,
        created_by: str,
    ) -> GovernanceProposal:
        for _attempt in range(3):
            current = await self.get_current(preview.difference_id, preview.difference_version)
            version = current.proposal_version + 1 if current is not None else 1
            record = GovernanceProposalRecord(
                task_id=task_id,
                tenant_id=tenant_id,
                difference_id=preview.difference_id,
                difference_version=preview.difference_version,
                analysis_id=analysis_id,
                analysis_version=analysis_version,
                proposal_version=version,
                proposal_source=preview.proposal_source.value,
                operation_type=preview.operation_type.value,
                target_entity_id=preview.target_entity_id,
                changes=[change.model_dump(mode="json") for change in preview.changes],
                rationale=preview.rationale,
                evidence_refs=list(preview.evidence_refs),
                risk=preview.risk.value,
                created_by=created_by,
                status=ProposalStatus.PENDING_EXECUTION.value,
                supersedes_id=current.id if current is not None else None,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(record)
                    await self.session.flush()
            except IntegrityError:
                continue
            return self._proposal(record)
        raise RuntimeError("could not allocate governance proposal version")

    async def count_for_difference(self, difference_id: UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(GovernanceProposalRecord)
                .where(GovernanceProposalRecord.difference_id == difference_id)
            )
            or 0
        )

    @staticmethod
    def _proposal(record: GovernanceProposalRecord) -> GovernanceProposal:
        created_at = record.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return GovernanceProposal.model_validate(
            {
                "id": record.id,
                "task_id": record.task_id,
                "tenant_id": record.tenant_id,
                "difference_id": record.difference_id,
                "difference_version": record.difference_version,
                "analysis_id": record.analysis_id,
                "analysis_version": record.analysis_version,
                "proposal_version": record.proposal_version,
                "proposal_source": record.proposal_source,
                "operation_type": record.operation_type,
                "target_entity_id": record.target_entity_id,
                "changes": record.changes,
                "rationale": record.rationale,
                "evidence_refs": record.evidence_refs,
                "risk": record.risk,
                "created_by": record.created_by,
                "created_at": created_at,
                "status": record.status,
                "supersedes_id": record.supersedes_id,
            }
        )

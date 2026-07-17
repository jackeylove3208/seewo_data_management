from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import OperatorContext
from app.governance.field_policy import editable_fields
from app.repositories.analyses import CURRENT_ANALYSIS_VERSION, AnalysisRepository
from app.repositories.differences import DifferenceRepository
from app.repositories.proposals import ProposalRepository
from app.schemas.differences import DifferenceItem
from app.schemas.governance import (
    AnalysisResult,
    AnalysisStatus,
    CauseAnalysisV2,
    ProposedFieldChange,
    RecommendedAction,
    RiskLevel,
)
from app.schemas.proposals import (
    CreateAIProposalRequest,
    CreateManualProposalRequest,
    GovernanceProposal,
    GovernanceProposalPreview,
    ProposalSource,
)


class ProposalConflict(ValueError):
    pass


class ProposalService:
    def __init__(self, session: AsyncSession, *, operator: OperatorContext) -> None:
        self.session = session
        self.operator = operator
        self.differences = DifferenceRepository(session)
        self.analyses = AnalysisRepository(session)
        self.proposals = ProposalRepository(session)

    async def preview_ai(
        self, difference_id: UUID, request: CreateAIProposalRequest
    ) -> GovernanceProposalPreview:
        difference = await self._require_difference(difference_id)
        self._require_version(difference, request.expected_difference_version)
        analysis = await self._require_analysis(difference, request.analysis_id)
        assert isinstance(analysis.output, CauseAnalysisV2)
        option = next(
            (item for item in analysis.output.options if item.option_id == request.option_id),
            None,
        )
        if option is None:
            raise ProposalConflict("analysis option not found")
        return GovernanceProposalPreview(
            difference_id=difference.id,
            difference_version=difference.version,
            proposal_source=ProposalSource.AI,
            operation_type=option.operation_type,
            target_entity_id=option.target_entity_id,
            changes=option.proposed_changes,
            rationale=option.rationale,
            evidence_refs=option.evidence_refs,
            risk=option.risk,
        )

    async def confirm_ai(
        self, difference_id: UUID, request: CreateAIProposalRequest
    ) -> GovernanceProposal:
        difference = await self._require_difference(difference_id)
        preview = await self.preview_ai(difference_id, request)
        analysis = await self._require_analysis(difference, request.analysis_id)
        return await self.proposals.create(
            task_id=difference.task_id,
            tenant_id=difference.tenant_id,
            analysis_id=analysis.id,
            analysis_version=analysis.analysis_version,
            preview=preview,
            created_by=self.operator.operator_id,
        )

    async def preview_manual(
        self, difference_id: UUID, request: CreateManualProposalRequest
    ) -> GovernanceProposalPreview:
        difference = await self._require_difference(difference_id)
        self._require_version(difference, request.expected_difference_version)
        await self._require_current_analysis(difference)
        if request.target_entity_id != difference.evidence.target_entity_id:
            raise ProposalConflict("target entity does not match current difference")
        allowed = editable_fields(difference.entity_type)
        unsupported = sorted(set(request.changes).difference(allowed))
        if unsupported:
            raise ProposalConflict(f"field is not editable: {', '.join(unsupported)}")
        target = difference.evidence.target_payload or {}
        compared_values = {field.field: field.target_value for field in difference.evidence.fields}
        changes = tuple(
            ProposedFieldChange(
                field=field,
                before=compared_values.get(field, target.get(field)),
                after=after,
            )
            for field, after in request.changes.items()
            if compared_values.get(field, target.get(field)) != after
        )
        if not changes:
            raise ProposalConflict("manual proposal has no effective changes")
        return GovernanceProposalPreview(
            difference_id=difference.id,
            difference_version=difference.version,
            proposal_source=ProposalSource.OPERATOR,
            operation_type=request.operation_type,
            target_entity_id=request.target_entity_id,
            changes=changes,
            rationale=request.rationale,
            evidence_refs=tuple(f"field:{change.field}" for change in changes),
            risk=self._manual_risk(request.operation_type, changes),
        )

    async def confirm_manual(
        self, difference_id: UUID, request: CreateManualProposalRequest
    ) -> GovernanceProposal:
        difference = await self._require_difference(difference_id)
        preview = await self.preview_manual(difference_id, request)
        analysis = await self._require_current_analysis(difference)
        return await self.proposals.create(
            task_id=difference.task_id,
            tenant_id=difference.tenant_id,
            analysis_id=analysis.id,
            analysis_version=analysis.analysis_version,
            preview=preview,
            created_by=self.operator.operator_id,
        )

    async def _require_difference(self, difference_id: UUID) -> DifferenceItem:
        difference = await self.differences.get(difference_id)
        if difference is None or difference.tenant_id != self.operator.tenant_id:
            raise LookupError("difference not found")
        return difference

    @staticmethod
    def _require_version(difference: DifferenceItem, expected_version: int) -> None:
        if difference.version != expected_version:
            raise ProposalConflict("difference version is stale")

    async def _require_analysis(
        self, difference: DifferenceItem, analysis_id: UUID
    ) -> AnalysisResult:
        analysis = await self._require_current_analysis(difference)
        if analysis.id != analysis_id:
            raise ProposalConflict("analysis does not match the current difference")
        return analysis

    async def _require_current_analysis(self, difference: DifferenceItem) -> AnalysisResult:
        analysis = await self.analyses.get_for_difference(
            difference.id, difference.version, CURRENT_ANALYSIS_VERSION
        )
        if (
            analysis is None
            or analysis.status not in {AnalysisStatus.SUCCEEDED, AnalysisStatus.MANUAL_REVIEW}
            or not isinstance(analysis.output, CauseAnalysisV2)
        ):
            raise ProposalConflict("current analysis is not available")
        return analysis

    @staticmethod
    def _manual_risk(
        operation_type: RecommendedAction, changes: tuple[ProposedFieldChange, ...]
    ) -> RiskLevel:
        high_risk_fields = {"parent_source_id", "department_source_id", "class_source_id"}
        if operation_type in {RecommendedAction.DISABLE, RecommendedAction.MOVE} or any(
            change.field in high_risk_fields for change in changes
        ):
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM

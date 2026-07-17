from app.governance.field_policy import editable_fields
from app.schemas.differences import DifferenceItem, DifferenceType
from app.schemas.governance import (
    CauseAnalysisV2,
    GovernanceOption,
    ProposedFieldChange,
    RecommendedAction,
    RiskLevel,
)


class DeterministicAnalysis:
    def for_difference(self, difference: DifferenceItem) -> CauseAnalysisV2 | None:
        if difference.difference_type is DifferenceType.SEEWO_MISSING:
            source = difference.evidence.source_payload or {}
            changes = tuple(
                ProposedFieldChange(field=field, before=None, after=source.get(field))
                for field in sorted(editable_fields(difference.entity_type).intersection(source))
                if source.get(field) is not None
            )
            return CauseAnalysisV2(
                cause="Authoritative entity has no accepted Seewo mapping",
                evidence_summary="No compatible target entity was accepted for this source entity",
                manual_only=False,
                options=(
                    GovernanceOption(
                        option_id="create-authoritative-entity",
                        operation_type=RecommendedAction.CREATE,
                        proposed_changes=changes,
                        rationale="Create the missing entity from the authoritative snapshot",
                        evidence_refs=("source_entity",),
                        risk=RiskLevel.MEDIUM,
                        confidence=1,
                        recommended=True,
                    ),
                ),
            )
        if difference.difference_type is DifferenceType.SEEWO_REDUNDANT:
            return CauseAnalysisV2(
                cause="Target entity is unconsumed in a complete reconciliation scope",
                evidence_summary="No authoritative entity consumed this target entity",
                manual_only=True,
                manual_reason=(
                    "Disabling an unmatched target entity is high risk and requires review"
                ),
            )
        return None

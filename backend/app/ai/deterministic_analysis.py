from app.schemas.differences import DifferenceItem, DifferenceType
from app.schemas.governance import CauseAnalysis, RecommendedAction, RiskLevel


class DeterministicAnalysis:
    def for_difference(self, difference: DifferenceItem) -> CauseAnalysis | None:
        if difference.difference_type is DifferenceType.SEEWO_MISSING:
            return CauseAnalysis(
                cause="Authoritative entity has no accepted Seewo mapping",
                evidence_summary="No compatible target entity was accepted for this source entity",
                recommended_action=RecommendedAction.CREATE,
                risk=RiskLevel.MEDIUM,
                confidence=1,
            )
        if difference.difference_type is DifferenceType.SEEWO_REDUNDANT:
            return CauseAnalysis(
                cause="Target entity is unconsumed in a complete reconciliation scope",
                evidence_summary="No authoritative entity consumed this target entity",
                recommended_action=RecommendedAction.DISABLE,
                risk=RiskLevel.HIGH,
                confidence=0.95,
            )
        return None

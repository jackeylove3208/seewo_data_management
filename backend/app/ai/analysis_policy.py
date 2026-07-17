from app.schemas.differences import DifferenceItem, DifferenceType
from app.schemas.governance import CauseAnalysis, RecommendedAction


class AnalysisPolicyError(ValueError):
    pass


ACTION_POLICY: dict[DifferenceType, frozenset[RecommendedAction]] = {
    DifferenceType.SEEWO_MISSING: frozenset(
        {RecommendedAction.CREATE, RecommendedAction.MANUAL_REVIEW}
    ),
    DifferenceType.SEEWO_REDUNDANT: frozenset(
        {
            RecommendedAction.DISABLE,
            RecommendedAction.SKIP,
            RecommendedAction.MANUAL_REVIEW,
        }
    ),
    DifferenceType.ATTRIBUTE_CONFLICT: frozenset(
        {
            RecommendedAction.UPDATE,
            RecommendedAction.SKIP,
            RecommendedAction.MANUAL_REVIEW,
        }
    ),
    DifferenceType.STRUCTURE_CONFLICT: frozenset(
        {
            RecommendedAction.MOVE,
            RecommendedAction.SKIP,
            RecommendedAction.MANUAL_REVIEW,
        }
    ),
    DifferenceType.DUPLICATE_CONFLICT: frozenset(
        {RecommendedAction.SKIP, RecommendedAction.MANUAL_REVIEW}
    ),
}


def validate_analysis_action(
    difference: DifferenceItem,
    analysis: CauseAnalysis,
) -> None:
    if analysis.recommended_action not in ACTION_POLICY[difference.difference_type]:
        raise AnalysisPolicyError(
            f"{analysis.recommended_action.value} is not allowed for "
            f"{difference.difference_type.value}"
        )

from app.governance.field_policy import editable_fields
from app.schemas.differences import DifferenceItem, DifferenceType
from app.schemas.governance import (
    CauseAnalysis,
    CauseAnalysisV2,
    RecommendedAction,
    RiskLevel,
)


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


def validate_analysis_options(
    difference: DifferenceItem,
    analysis: CauseAnalysisV2,
) -> None:
    if analysis.manual_only:
        return
    allowed_actions = ACTION_POLICY[difference.difference_type] - {RecommendedAction.MANUAL_REVIEW}
    field_evidence = {field.field: field for field in difference.evidence.fields}
    allowed_evidence = {
        "source_entity",
        "target_entity",
        *(f"field:{name}" for name in field_evidence),
    }
    for option in analysis.options:
        if option.operation_type not in allowed_actions:
            raise AnalysisPolicyError(
                f"{option.operation_type.value} is not allowed for "
                f"{difference.difference_type.value}"
            )
        if option.risk is RiskLevel.HIGH:
            raise AnalysisPolicyError("high-risk analysis must route to manual review")
        _validate_target(difference, option.operation_type, option.target_entity_id)
        if not set(option.evidence_refs).issubset(allowed_evidence):
            raise AnalysisPolicyError("analysis option contains an unknown evidence reference")
        if option.operation_type is RecommendedAction.SKIP:
            if option.proposed_changes:
                raise AnalysisPolicyError("skip option cannot contain field changes")
            continue
        for change in option.proposed_changes:
            if change.field not in editable_fields(difference.entity_type):
                raise AnalysisPolicyError(f"field is not editable: {change.field}")
            evidence = field_evidence.get(change.field)
            if evidence is not None:
                if change.before != evidence.target_value:
                    raise AnalysisPolicyError(f"before value drift for field: {change.field}")
                if change.after != evidence.source_value:
                    raise AnalysisPolicyError(
                        f"after value lacks authoritative evidence for field: {change.field}"
                    )
                continue
            source_payload = difference.evidence.source_payload or {}
            target_payload = difference.evidence.target_payload or {}
            if change.before != target_payload.get(change.field):
                raise AnalysisPolicyError(f"before value drift for field: {change.field}")
            if change.after != source_payload.get(change.field):
                raise AnalysisPolicyError(
                    f"after value lacks authoritative evidence for field: {change.field}"
                )


def _validate_target(
    difference: DifferenceItem,
    action: RecommendedAction,
    target_entity_id: object | None,
) -> None:
    expected = difference.evidence.target_entity_id
    if action is RecommendedAction.CREATE:
        if target_entity_id is not None:
            raise AnalysisPolicyError("create option cannot identify a target entity")
        return
    if target_entity_id != expected:
        raise AnalysisPolicyError("analysis option target entity does not match current evidence")

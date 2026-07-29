import re

from app.governance.field_policy import editable_fields
from app.schemas.differences import DifferenceItem, DifferenceType
from app.schemas.governance import (
    AutoExecutableResolution,
    CauseAnalysisV2,
    CauseAnalysisV3,
    ManualResolution,
    NeedsInformationResolution,
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


def validate_analysis_v3(
    difference: DifferenceItem,
    analysis: CauseAnalysisV3,
) -> None:
    _validate_chinese_content(analysis)
    for solution in analysis.solutions:
        if isinstance(solution, (NeedsInformationResolution, ManualResolution)):
            continue
        if not isinstance(solution, AutoExecutableResolution):
            raise AnalysisPolicyError("unknown resolution mode")
        if solution.risk is RiskLevel.HIGH:
            raise AnalysisPolicyError("high-risk analysis must route to manual review")
        action = solution.action
        allowed_actions = ACTION_POLICY[difference.difference_type] - {
            RecommendedAction.MANUAL_REVIEW
        }
        if action.operation_type not in allowed_actions:
            raise AnalysisPolicyError(
                f"{action.operation_type.value} is not allowed for "
                f"{difference.difference_type.value}"
            )
        _validate_target(difference, action.operation_type, action.target_entity_id)
        field_evidence = {field.field: field for field in difference.evidence.fields}
        allowed_evidence = {
            "source_entity",
            "target_entity",
            *(f"field:{name}" for name in field_evidence),
        }
        if not set(solution.evidence_refs).issubset(allowed_evidence):
            raise AnalysisPolicyError("analysis solution contains an unknown evidence reference")
        if not solution.evidence_refs:
            raise AnalysisPolicyError("executable resolution requires an evidence reference")
        if action.operation_type is RecommendedAction.SKIP:
            if action.proposed_changes:
                raise AnalysisPolicyError("skip solution cannot contain field changes")
            continue
        if not action.proposed_changes:
            raise AnalysisPolicyError("executable resolution requires a field change")
        for change in action.proposed_changes:
            if change.field not in editable_fields(difference.entity_type):
                raise AnalysisPolicyError(f"field is not editable: {change.field}")
            evidence = field_evidence.get(change.field)
            source_payload = difference.evidence.source_payload or {}
            target_payload = difference.evidence.target_payload or {}
            expected_before = (
                evidence.target_value if evidence is not None else target_payload.get(change.field)
            )
            expected_after = (
                evidence.source_value if evidence is not None else source_payload.get(change.field)
            )
            if change.before != expected_before:
                raise AnalysisPolicyError(f"before value drift for field: {change.field}")
            if change.after != expected_after:
                raise AnalysisPolicyError(
                    f"after value lacks authoritative evidence for field: {change.field}"
                )


_ALLOWED_LATIN_TERMS = frozenset({"ai", "api", "csv"})
_INTERNAL_VISIBLE_CODES = frozenset(
    {
        "create",
        "update",
        "move",
        "disable",
        "skip",
        "manual_review",
        "phone",
        "email",
        "parent_source_id",
        "source_id",
        "target_id",
    }
)


def _validate_chinese_content(analysis: CauseAnalysisV3) -> None:
    visible = [
        analysis.issue_title,
        analysis.cause_summary,
        analysis.evidence_summary,
        analysis.business_impact,
    ]
    for solution in analysis.solutions:
        visible.extend((solution.title, solution.rationale, solution.risk_reason))
        visible.extend(solution.preconditions)
        if isinstance(solution, NeedsInformationResolution):
            for request in solution.information_requests:
                visible.extend((request.question, request.reason, request.source_hint))
        elif isinstance(solution, ManualResolution):
            visible.extend(step.instruction for step in solution.manual_steps)
    for value in visible:
        if not any("\u4e00" <= character <= "\u9fff" for character in value):
            raise AnalysisPolicyError("user-visible text must be Simplified Chinese")
        latin_terms = {term.casefold() for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", value)}
        if latin_terms.difference(_ALLOWED_LATIN_TERMS):
            if latin_terms.intersection(_INTERNAL_VISIBLE_CODES):
                raise AnalysisPolicyError("user-visible text contains an internal code")
            raise AnalysisPolicyError("user-visible text must be Simplified Chinese")
        if latin_terms.intersection(_INTERNAL_VISIBLE_CODES):
            raise AnalysisPolicyError("user-visible text contains an internal code")


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

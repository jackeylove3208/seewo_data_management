from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.ai.analysis_policy import AnalysisPolicyError, validate_analysis_options
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import (
    DifferenceAction,
    DifferenceEvidence,
    DifferenceItem,
    DifferenceStatus,
    DifferenceType,
    FieldDifference,
)
from app.schemas.governance import (
    CauseAnalysisV2,
    GovernanceOption,
    ProposedFieldChange,
    RecommendedAction,
    RiskLevel,
)

TARGET_ID = uuid4()


def difference() -> DifferenceItem:
    return DifferenceItem(
        id=uuid4(),
        task_id=uuid4(),
        tenant_id="school-1",
        entity_type=EntityType.TEACHER,
        difference_type=DifferenceType.ATTRIBUTE_CONFLICT,
        proposed_action=DifferenceAction.UPDATE,
        evidence=DifferenceEvidence(
            source_snapshot_id=uuid4(),
            target_snapshot_id=uuid4(),
            source_entity_id=uuid4(),
            target_entity_id=TARGET_ID,
            fields=(
                FieldDifference(
                    field="phone",
                    source_value="13800000000",
                    target_value="13900000000",
                    normalized_source="13800000000",
                    normalized_target="13900000000",
                    comparison="attribute",
                ),
            ),
            source_payload={"entity_type": "teacher", "phone": "13800000000"},
            target_payload={"entity_type": "teacher", "phone": "13900000000"},
            comparison_rule_version="comparison-v1",
        ),
        status=DifferenceStatus.OPEN,
        created_at=datetime.now(UTC),
    )


def output(
    *,
    action: RecommendedAction = RecommendedAction.UPDATE,
    target_id=TARGET_ID,
    before="13900000000",
    after="13800000000",
    risk: RiskLevel = RiskLevel.LOW,
) -> CauseAnalysisV2:
    return CauseAnalysisV2(
        cause="The governed phone value differs",
        evidence_summary="The persisted field evidence contains both values",
        manual_only=False,
        options=(
            GovernanceOption(
                option_id="option-1",
                operation_type=action,
                target_entity_id=target_id,
                proposed_changes=(ProposedFieldChange(field="phone", before=before, after=after),),
                rationale="Use the authoritative phone value",
                evidence_refs=("field:phone",),
                risk=risk,
                confidence=0.9,
                recommended=True,
            ),
        ),
    )


def test_v2_policy_accepts_supported_authoritative_change() -> None:
    validate_analysis_options(difference(), output())


@pytest.mark.parametrize(
    ("analysis", "message"),
    [
        (output(action=RecommendedAction.DISABLE), "not allowed"),
        (output(target_id=uuid4()), "target entity"),
        (output(before="wrong"), "before value"),
        (output(after="13199999999"), "authoritative evidence"),
        (output(risk=RiskLevel.HIGH), "high-risk"),
    ],
)
def test_v2_policy_rejects_unsafe_model_options(
    analysis: CauseAnalysisV2,
    message: str,
) -> None:
    with pytest.raises(AnalysisPolicyError, match=message):
        validate_analysis_options(difference(), analysis)

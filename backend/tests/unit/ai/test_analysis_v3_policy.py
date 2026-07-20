from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.ai.analysis_policy import AnalysisPolicyError, validate_analysis_v3
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
    AutoExecutableResolution,
    CauseAnalysisV3,
    ManualResolution,
    ManualStep,
    ProposedFieldChange,
    RecommendedAction,
    ResolutionAction,
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


def output(*, risk: RiskLevel = RiskLevel.LOW, cause: str = "双方手机号记录不一致。"):
    return CauseAnalysisV3(
        locale="zh-CN",
        issue_title="教师手机号不一致",
        cause_summary=cause,
        evidence_summary="权威快照和希沃快照中的手机号字段不同。",
        business_impact="教师可能无法收到教学通知。",
        recommended_solution_id="solution-1",
        solutions=(
            AutoExecutableResolution(
                solution_id="solution-1",
                title="更新教师手机号",
                rationale="采用第三方权威记录中的手机号。",
                risk=risk,
                risk_reason="只修改已匹配教师的手机号。",
                confidence=0.96,
                evidence_refs=("field:phone",),
                recommended=True,
                action=ResolutionAction(
                    operation_type=RecommendedAction.UPDATE,
                    target_entity_id=TARGET_ID,
                    proposed_changes=(
                        ProposedFieldChange(
                            field="phone",
                            before="13900000000",
                            after="13800000000",
                        ),
                    ),
                ),
            ),
        ),
    )


def test_v3_policy_accepts_safe_chinese_executable_resolution() -> None:
    validate_analysis_v3(difference(), output())


def test_v3_policy_rejects_english_user_visible_text() -> None:
    with pytest.raises(AnalysisPolicyError, match="Simplified Chinese"):
        validate_analysis_v3(difference(), output(cause="Phone values do not match"))


def test_v3_policy_rejects_mixed_english_user_visible_text() -> None:
    with pytest.raises(AnalysisPolicyError, match="Simplified Chinese"):
        validate_analysis_v3(difference(), output(cause="Mismatch 字段与希沃记录不一致。"))


def test_v3_policy_rejects_internal_codes_in_user_visible_text() -> None:
    with pytest.raises(AnalysisPolicyError, match="internal code"):
        validate_analysis_v3(difference(), output(cause="需要更新 phone 字段。"))


def test_v3_policy_rejects_high_risk_executable_resolution() -> None:
    with pytest.raises(AnalysisPolicyError, match="high-risk"):
        validate_analysis_v3(difference(), output(risk=RiskLevel.HIGH))


def test_v3_policy_rejects_executable_resolution_without_evidence() -> None:
    analysis = output()
    solution = analysis.solutions[0]

    with pytest.raises(AnalysisPolicyError, match="evidence reference"):
        validate_analysis_v3(
            difference(),
            analysis.model_copy(
                update={"solutions": (solution.model_copy(update={"evidence_refs": ()}),)}
            ),
        )


def test_v3_policy_rejects_unknown_evidence_reference() -> None:
    analysis = output()
    solution = analysis.solutions[0]

    with pytest.raises(AnalysisPolicyError, match="unknown evidence reference"):
        validate_analysis_v3(
            difference(),
            analysis.model_copy(
                update={
                    "solutions": (solution.model_copy(update={"evidence_refs": ("field:email",)}),)
                }
            ),
        )


def test_v3_policy_rejects_target_mismatch() -> None:
    analysis = output()
    solution = analysis.solutions[0]

    with pytest.raises(AnalysisPolicyError, match="target entity does not match"):
        validate_analysis_v3(
            difference(),
            analysis.model_copy(
                update={
                    "solutions": (
                        solution.model_copy(
                            update={
                                "action": solution.action.model_copy(
                                    update={"target_entity_id": uuid4()}
                                )
                            }
                        ),
                    )
                }
            ),
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            ProposedFieldChange(
                field="phone",
                before="13700000000",
                after="13800000000",
            ),
            "before value drift",
        ),
        (
            ProposedFieldChange(
                field="phone",
                before="13900000000",
                after="13700000000",
            ),
            "authoritative evidence",
        ),
        (
            ProposedFieldChange(field="password", before=None, after=None),
            "field is not editable",
        ),
    ],
)
def test_v3_policy_rejects_unsafe_field_change(
    change: ProposedFieldChange,
    message: str,
) -> None:
    analysis = output()
    solution = analysis.solutions[0]

    with pytest.raises(AnalysisPolicyError, match=message):
        validate_analysis_v3(
            difference(),
            analysis.model_copy(
                update={
                    "solutions": (
                        solution.model_copy(
                            update={
                                "action": solution.action.model_copy(
                                    update={"proposed_changes": (change,)}
                                )
                            }
                        ),
                    )
                }
            ),
        )


def test_v3_policy_rejects_update_without_effective_changes() -> None:
    analysis = output()
    solution = analysis.solutions[0]

    with pytest.raises(AnalysisPolicyError, match="field change"):
        validate_analysis_v3(
            difference(),
            analysis.model_copy(
                update={
                    "solutions": (
                        solution.model_copy(
                            update={
                                "action": solution.action.model_copy(
                                    update={"proposed_changes": ()}
                                )
                            }
                        ),
                    )
                }
            ),
        )


def test_v3_policy_accepts_actionable_manual_resolution() -> None:
    manual = CauseAnalysisV3(
        locale="zh-CN",
        issue_title="教师身份无法确认",
        cause_summary="现有记录缺少稳定工号，无法确认是否为同一教师。",
        evidence_summary="候选记录姓名相同，但手机号和所属组织均不一致。",
        business_impact="直接修改可能影响其他教师账号。",
        recommended_solution_id="manual-1",
        solutions=(
            ManualResolution(
                solution_id="manual-1",
                title="人工核对教师身份",
                rationale="先确认身份，再决定是否修改。",
                risk=RiskLevel.HIGH,
                risk_reason="身份错误会修改错误账号。",
                confidence=0.2,
                recommended=True,
                manual_steps=(
                    ManualStep(order=1, instruction="向学校管理员核对教师工号。"),
                    ManualStep(order=2, instruction="确认后通过人工编辑器生成待执行方案。"),
                ),
            ),
        ),
    )

    validate_analysis_v3(difference(), manual)

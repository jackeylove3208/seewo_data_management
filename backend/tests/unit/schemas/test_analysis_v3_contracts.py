from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.governance import (
    AutoExecutableResolution,
    CauseAnalysisV3,
    InformationRequest,
    ManualResolution,
    ManualStep,
    NeedsInformationResolution,
    ProposedFieldChange,
    RecommendedAction,
    ResolutionAction,
    RiskLevel,
)


def auto_resolution(
    *,
    solution_id: str = "solution-1",
    recommended: bool = True,
) -> AutoExecutableResolution:
    return AutoExecutableResolution(
        solution_id=solution_id,
        title="更新教师手机号",
        rationale="以第三方权威记录中的手机号为准，修正希沃中的旧值。",
        risk=RiskLevel.LOW,
        risk_reason="仅修改已确认教师的一项联系方式。",
        confidence=0.96,
        evidence_refs=("field:phone",),
        preconditions=("教师对应关系仍然有效",),
        recommended=recommended,
        action=ResolutionAction(
            operation_type=RecommendedAction.UPDATE,
            target_entity_id=uuid4(),
            proposed_changes=(
                ProposedFieldChange(
                    field="phone",
                    before="13900000000",
                    after="13800000000",
                ),
            ),
        ),
    )


def analysis(*solutions) -> CauseAnalysisV3:
    return CauseAnalysisV3(
        locale="zh-CN",
        issue_title="教师手机号不一致",
        cause_summary="第三方权威记录与希沃保存的手机号不同。",
        evidence_summary="系统比对了同一教师在双方快照中的手机号字段。",
        business_impact="可能导致教师无法收到正确的教学通知。",
        recommended_solution_id=solutions[0].solution_id if solutions else "solution-1",
        solutions=solutions,
    )


def test_analysis_v3_requires_at_least_one_resolution_path() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        analysis()


def test_analysis_v3_requires_exactly_one_recommended_resolution() -> None:
    with pytest.raises(ValidationError, match="exactly one solution"):
        analysis(
            auto_resolution(),
            auto_resolution(solution_id="solution-2", recommended=True),
        )


def test_analysis_v3_recommended_id_must_reference_recommended_solution() -> None:
    with pytest.raises(ValidationError, match="recommended solution id"):
        CauseAnalysisV3(
            locale="zh-CN",
            issue_title="教师手机号不一致",
            cause_summary="双方手机号不同。",
            evidence_summary="已核对手机号字段。",
            business_impact="通知可能发送失败。",
            recommended_solution_id="missing",
            solutions=(auto_resolution(),),
        )


def test_information_resolution_requires_a_concrete_request() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        NeedsInformationResolution(
            solution_id="solution-info",
            title="补充教师身份信息",
            rationale="现有证据无法确认双方记录属于同一教师。",
            risk=RiskLevel.MEDIUM,
            risk_reason="错误合并会影响其他教师账号。",
            confidence=0.4,
            recommended=True,
            information_requests=(),
        )


def test_manual_resolution_requires_ordered_steps() -> None:
    result = analysis(
        ManualResolution(
            solution_id="solution-manual",
            title="人工核对教师身份",
            rationale="当前证据不足，不能安全生成自动修改。",
            risk=RiskLevel.HIGH,
            risk_reason="错误修改可能影响教师登录。",
            confidence=0.3,
            recommended=True,
            manual_steps=(
                ManualStep(order=1, instruction="向学校管理员核对教师工号。"),
                ManualStep(order=2, instruction="确认身份后在人工编辑器中填写修改。"),
            ),
        )
    )

    assert isinstance(result.solutions[0], ManualResolution)


def test_needs_information_resolution_accepts_specific_questions() -> None:
    result = analysis(
        NeedsInformationResolution(
            solution_id="solution-info",
            title="补充教师工号",
            rationale="姓名相同但缺少稳定身份标识。",
            risk=RiskLevel.MEDIUM,
            risk_reason="仅凭姓名可能匹配到错误教师。",
            confidence=0.5,
            recommended=True,
            information_requests=(
                InformationRequest(
                    request_type="teacher_number",
                    question="这两条记录是否属于同一教师？",
                    reason="需要稳定工号确认身份。",
                    source_hint="学校教师花名册",
                ),
            ),
        )
    )

    assert result.solutions[0].mode == "needs_information"

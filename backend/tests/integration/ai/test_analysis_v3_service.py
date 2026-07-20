from datetime import UTC, datetime

import pytest

from app.ai.agent import AgentResult
from app.ai.analysis_service import AnalysisService
from app.ai.providers.base import ModelUsage
from app.schemas.differences import DifferenceType
from app.schemas.governance import (
    AnalysisProvenance,
    AnalysisStatus,
    AutoExecutableResolution,
    CauseAnalysisV3,
    ManualResolution,
    ProposedFieldChange,
    RecommendedAction,
    ResolutionAction,
    RiskLevel,
)
from tests.integration.ai.test_analysis_service import seed_difference


class V3AgentSpy:
    def __init__(self, *, invalid_action: bool = False) -> None:
        self.invalid_action = invalid_action
        self.requests = []

    async def analyze(self, request):
        self.requests.append(request)
        evidence = request.input_payload["evidence"]
        field = evidence["fields"][0]
        output = CauseAnalysisV3(
            locale="zh-CN",
            issue_title="教师手机号不一致",
            cause_summary="第三方权威记录与希沃中的手机号不同。",
            evidence_summary="已比对双方快照中同一教师的手机号字段。",
            business_impact="教师可能无法收到教学通知。",
            recommended_solution_id="solution-1",
            solutions=(
                AutoExecutableResolution(
                    solution_id="solution-1",
                    title="更新教师手机号",
                    rationale="采用第三方权威记录中的手机号。",
                    risk=RiskLevel.LOW,
                    risk_reason="仅修改已确认教师的一项联系方式。",
                    confidence=0.96,
                    evidence_refs=(f"field:{field['field']}",),
                    recommended=True,
                    action=ResolutionAction(
                        operation_type=(
                            RecommendedAction.DISABLE
                            if self.invalid_action
                            else RecommendedAction.UPDATE
                        ),
                        target_entity_id=evidence["target_entity_id"],
                        proposed_changes=(
                            ProposedFieldChange(
                                field=field["field"],
                                before=field["target_value"],
                                after=field["source_value"],
                            ),
                        ),
                    ),
                ),
            ),
        )
        return AgentResult(
            output=output,
            provenance=AnalysisProvenance(
                provider="agent-provider",
                model="agent-model",
                skill_name="analyze-data-difference",
                skill_version="1.0.0",
                prompt_version="analysis-prompt-v3",
                usage=ModelUsage(input_tokens=5, output_tokens=3),
                generated_at=datetime.now(UTC),
            ),
        )


@pytest.mark.asyncio
async def test_v3_missing_case_uses_chinese_deterministic_resolution(session) -> None:
    difference = await seed_difference(session, DifferenceType.SEEWO_MISSING)
    agent = V3AgentSpy()

    result = await AnalysisService(session, agent=agent).analyze_v3(difference.id)

    assert result.analysis_version == "analysis-v3"
    assert result.status is AnalysisStatus.SUCCEEDED
    assert isinstance(result.output, CauseAnalysisV3)
    assert "希沃" in result.output.cause_summary
    assert isinstance(result.output.solutions[0], AutoExecutableResolution)
    assert agent.requests == []


@pytest.mark.asyncio
async def test_v3_semantic_case_persists_agent_resolution(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    agent = V3AgentSpy()

    result = await AnalysisService(session, agent=agent).analyze_v3(difference.id)

    assert result.status is AnalysisStatus.SUCCEEDED
    assert isinstance(result.output, CauseAnalysisV3)
    assert result.output.recommended_solution_id == "solution-1"
    assert agent.requests[0].analysis_version == "analysis-v3"


@pytest.mark.asyncio
async def test_v3_invalid_output_retries_with_feedback_then_falls_back(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    agent = V3AgentSpy(invalid_action=True)

    result = await AnalysisService(session, agent=agent).analyze_v3(difference.id)

    assert result.status is AnalysisStatus.MANUAL_REVIEW
    assert isinstance(result.output, CauseAnalysisV3)
    assert isinstance(result.output.solutions[0], ManualResolution)
    assert result.failure_code == "analysis_policy_error"
    assert len(agent.requests) == 2
    assert agent.requests[1].input_payload["validation_feedback"] == "analysis_policy_error"

from uuid import uuid4

import pytest

from app.ai.agent_analysis_service import AgentAnalysisService, AgentAnalysisWorkItem
from app.ai.providers.base import LLMRequest, LLMResponse


class CapturingProvider:
    def __init__(self, output):
        self.output = output
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(output=self.output, provider="stub", model="stub")


@pytest.mark.asyncio
async def test_model_input_tokenizes_student_phone_and_validates_response_membership() -> None:
    work_item_id = uuid4()
    provider = CapturingProvider(
        {
            "findings": [
                {
                    "work_item_id": str(work_item_id), "kind": "target_extra",
                    "category_zh": "希沃多余", "analysis_zh": "无权威匹配。",
                    "evidence_refs": ["input:csv:2"],
                    "solutions": [
                        {
                            "operation": "delete",
                            "risk": "high",
                            "solution_zh": "删除。",
                            "recommended": True,
                        }
                    ],
                }
            ]
        }
    )
    service = AgentAnalysisService(provider, tokenization_secret="s" * 16)

    findings = await service.analyze(
        tenant_id="school-1", task_id=uuid4(),
        work_items=(
            AgentAnalysisWorkItem(
                work_item_id=work_item_id, kind="target_extra", entity_kind="student",
                locator="csv:2", fields={"name": "李四", "phone": "13800138000"},
            ),
        ),
    )

    assert findings[0].work_item_id == work_item_id
    request_content = provider.requests[0].messages[-1].content
    assert "13800138000" not in request_content
    assert "STUDENT_PHONE_" in request_content

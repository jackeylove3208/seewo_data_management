import json
from collections import deque
from uuid import uuid4

import pytest

from app.ai.agent import (
    AgentRequest,
    GovernanceAgent,
    InvalidAgentOutput,
    ToolLimitExceeded,
    UnsafeToolCall,
)
from app.ai.mcp.authorization import ToolContext
from app.ai.mcp.server import ToolResult
from app.ai.providers.base import LLMResponse, ModelUsage
from app.schemas.governance import RecommendedAction, RiskLevel


def response(output: dict) -> LLMResponse:
    return LLMResponse(
        output={"result": output},
        provider="model-provider",
        model="model-v1",
        usage=ModelUsage(input_tokens=5, output_tokens=3),
        request_id=str(uuid4()),
    )


def valid_output() -> dict:
    return {
        "cause": "Candidate identity remains ambiguous",
        "evidence_summary": "Two candidates have similar scores",
        "recommended_action": "manual_review",
        "risk": "high",
        "confidence": 0.6,
    }


class ModelStub:
    def __init__(self, outputs: list[LLMResponse]) -> None:
        self.outputs = deque(outputs)
        self.requests = []

    async def complete_json(self, request):
        self.requests.append(request)
        return self.outputs.popleft()


class ToolGatewayStub:
    def __init__(self) -> None:
        self.calls = []

    async def call(self, name, arguments, context):
        self.calls.append((name, arguments, context))
        return ToolResult(payload={"items": []}, trace_id=f"trace-{len(self.calls)}")


def request() -> AgentRequest:
    difference_id = uuid4()
    return AgentRequest(
        skill_name="analyze-data-difference",
        skill_version="1.0.0",
        input_payload={"difference_id": str(difference_id), "name": "张三"},
        tool_context=ToolContext(
            operator_id="operator-1",
            tenant_id="school-1",
            task_id=uuid4(),
            allowed_difference_ids=frozenset({difference_id}),
        ),
    )


@pytest.mark.asyncio
async def test_agent_returns_validated_output_and_fixed_provenance() -> None:
    model = ModelStub([response(valid_output())])
    result = await GovernanceAgent(model, ToolGatewayStub()).analyze(request())

    assert result.output.recommended_action is RecommendedAction.MANUAL_REVIEW
    assert result.output.risk is RiskLevel.HIGH
    assert result.provenance.provider == "model-provider"
    assert result.provenance.skill_version == "1.0.0"
    assert result.provenance.prompt_version == "analysis-prompt-v1"
    assert result.provenance.usage.input_tokens == 5


@pytest.mark.asyncio
async def test_agent_calls_only_a_skill_allowed_tool() -> None:
    model = ModelStub(
        [
            response(
                {
                    "tool_call": {
                        "name": "candidate_search",
                        "arguments": {
                            "difference_id": "ignored",
                            "query": "张三",
                            "top_k": 5,
                        },
                    }
                }
            ),
            response(valid_output()),
        ]
    )
    tools = ToolGatewayStub()

    result = await GovernanceAgent(model, tools).analyze(request())

    assert [call[0] for call in tools.calls] == ["candidate_search"]
    assert result.provenance.tool_trace_ids == ("trace-1",)
    assert result.provenance.usage.input_tokens == 10
    first_request = model.requests[0]
    assert "tool_call" in json.dumps(first_request.response_schema)
    assert "execution_context" not in json.dumps(first_request.response_schema)
    assert "candidate_search" in first_request.messages[0].content
    assert "mapping_rules" in first_request.messages[0].content
    assert "execution_context" not in first_request.messages[0].content
    assert '"tool_call"' in first_request.messages[0].content
    second_request = model.requests[1]
    assert [message.role for message in second_request.messages[-2:]] == [
        "assistant",
        "user",
    ]
    assert '"tool_result"' in second_request.messages[-1].content


@pytest.mark.asyncio
async def test_agent_rejects_a_tool_not_listed_by_the_skill() -> None:
    model = ModelStub([response({"tool_call": {"name": "apply_target_update", "arguments": {}}})])

    with pytest.raises(UnsafeToolCall, match="apply_target_update"):
        await GovernanceAgent(model, ToolGatewayStub()).analyze(request())


@pytest.mark.asyncio
async def test_agent_enforces_tool_call_limit() -> None:
    model = ModelStub(
        [
            response(
                {
                    "tool_call": {
                        "name": "mapping_rules",
                        "arguments": {"difference_id": str(uuid4())},
                    }
                }
            )
            for _ in range(5)
        ]
    )

    with pytest.raises(ToolLimitExceeded, match="4"):
        await GovernanceAgent(model, ToolGatewayStub()).analyze(request())


@pytest.mark.asyncio
async def test_agent_rejects_invalid_structured_output() -> None:
    model = ModelStub([response({"cause": "incomplete"})])

    with pytest.raises(InvalidAgentOutput) as caught:
        await GovernanceAgent(model, ToolGatewayStub()).analyze(request())

    assert caught.value.provenance.provider == "model-provider"
    assert caught.value.provenance.model == "model-v1"
    assert caught.value.provenance.usage.input_tokens == 5
    assert caught.value.provenance.usage.output_tokens == 3

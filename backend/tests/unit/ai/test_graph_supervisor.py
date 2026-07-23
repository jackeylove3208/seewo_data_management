import pytest

from app.agent_graph.contracts import (
    AllowedActionSetV1,
    AllowedActionV1,
    SupervisorContextV1,
)
from app.ai.graph_supervisor import GraphSupervisorAgent, GraphSupervisorFailure
from app.ai.providers.base import LLMRequest, LLMResponse, TransientModelError


class ScriptedProvider:
    def __init__(self, outputs: list[dict] | None = None, *, failures: int = 0) -> None:
        self.outputs = list(outputs or [])
        self.failures = failures
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.failures:
            self.failures -= 1
            raise TransientModelError("temporary")
        return LLMResponse(
            output={"result": self.outputs.pop(0)},
            provider="scripted",
            model="test-supervisor",
            request_id=f"request-{len(self.requests)}",
        )


def _action(action_id: str, evidence: str, successor: str) -> AllowedActionV1:
    return AllowedActionV1(
        action_id=action_id,
        kind="dispatch_sub_agent",
        sub_agent="source-inspection",
        resource_ids=(f"resource:{action_id}",),
        required_evidence=(evidence,),
        risk="low",
        requires_human=False,
        successor_node=successor,
    )


def _context() -> SupervisorContextV1:
    return SupervisorContextV1(
        tenant_ref="tenant-ref:demo",
        task_id="task:1",
        run_id="run:1",
        run_kind="sync",
        workflow_version="agent-graph-v1",
        graph_version="agent-sync-graph-v1",
        current_node="inspect_sources",
        graph_cursor=0,
        status="running",
        action_set=AllowedActionSetV1(
            allowed_actions=(
                _action(
                    "inspect_authority",
                    "authority-inspection-v1",
                    "inspect_sources",
                ),
                _action("inspect_target", "target-inspection-v1", "inspect_sources"),
            ),
            action_set_hash="sha256:" + ("a" * 64),
        ),
    )


def _decision(action_id: str) -> dict:
    other = "inspect_target" if action_id == "inspect_authority" else "inspect_authority"
    evidence = (
        "authority-inspection-v1"
        if action_id == "inspect_authority"
        else "target-inspection-v1"
    )
    return {
        "action_id": action_id,
        "reason_zh": "选择当前证据准备充分的来源。",
        "expected_result": evidence,
        "observed_blockers": [],
        "risk_notes_zh": ["当前动作只读取服务端授权证据。"],
        "why_not_other_actions_zh": [
            {"action_id": other, "reason_zh": "另一个来源留到下一动作检查。"}
        ],
        "operator_message_zh": "正在检查数据来源。",
    }


@pytest.mark.asyncio
async def test_graph_supervisor_uses_pinned_skill_and_returns_member_decision() -> None:
    provider = ScriptedProvider([_decision("inspect_authority")])

    decision = await GraphSupervisorAgent(provider).decide(_context())

    assert decision.action_id == "inspect_authority"
    assert "orchestrate-controlled-agent-graph@1.0.0" in (
        provider.requests[0].messages[0].content
    )
    assert provider.requests[0].response_schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_different_model_choices_produce_different_decisions() -> None:
    first = await GraphSupervisorAgent(
        ScriptedProvider([_decision("inspect_authority")])
    ).decide(_context())
    second = await GraphSupervisorAgent(
        ScriptedProvider([_decision("inspect_target")])
    ).decide(_context())

    assert first.action_id != second.action_id


@pytest.mark.asyncio
async def test_graph_supervisor_rejects_incomplete_alternative_reasoning() -> None:
    output = _decision("inspect_authority")
    output["why_not_other_actions_zh"] = []

    with pytest.raises(GraphSupervisorFailure, match="invalid Supervisor decision"):
        await GraphSupervisorAgent(ScriptedProvider([output]), max_retries=0).decide(
            _context()
        )


@pytest.mark.asyncio
async def test_graph_supervisor_uses_initial_attempt_plus_three_retries() -> None:
    provider = ScriptedProvider([_decision("inspect_target")], failures=3)

    result = await GraphSupervisorAgent(provider, max_retries=3).decide(_context())

    assert result.action_id == "inspect_target"
    assert len(provider.requests) == 4


@pytest.mark.asyncio
async def test_graph_supervisor_fails_closed_after_retry_exhaustion() -> None:
    provider = ScriptedProvider(failures=4)

    with pytest.raises(GraphSupervisorFailure, match="after 4 attempts"):
        await GraphSupervisorAgent(provider, max_retries=3).decide(_context())


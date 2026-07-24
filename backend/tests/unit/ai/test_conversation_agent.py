from uuid import uuid4

import pytest

from app.ai.conversation_agent import (
    ConversationAgentContext,
    ConversationSupervisorAgent,
)
from app.ai.providers.base import LLMRequest, LLMResponse


class CapturingProvider:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(output=self.output, provider="stub", model="stub")


def _context(**overrides: object) -> ConversationAgentContext:
    values: dict[str, object] = {
        "conversation_id": uuid4(),
        "tenant_id": "school-1",
        "message": "同步七年级学生数据",
        "available_source_refs": ("third-party/roster.csv", "seewo/roster.csv"),
    }
    values.update(overrides)
    return ConversationAgentContext.model_validate(values)


@pytest.mark.asyncio
async def test_supervisor_uses_versioned_skill_and_returns_confirmation() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "七年级学生同步",
                "entity_types": ["student"],
                "source_ref": "third-party/roster.csv",
                "target_ref": "seewo/roster.csv",
                "message_zh": "已确认第三方和希沃数据来源。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(_context())

    assert decision.kind == "start_confirmation"
    assert decision.source_ref == "third-party/roster.csv"
    assert "converse-school-data-sync@1.0.0" in provider.requests[0].messages[0].content
    assert "不可信证据" in provider.requests[0].messages[0].content


@pytest.mark.asyncio
async def test_supervisor_rejects_source_not_returned_by_server_discovery() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "同步",
                "entity_types": ["student"],
                "source_ref": "../../.env",
                "target_ref": "seewo/roster.csv",
                "message_zh": "已确认。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(_context())

    assert decision.kind == "clarification"
    assert decision.source_ref is None


@pytest.mark.asyncio
async def test_supervisor_accepts_flat_json_object_provider_response() -> None:
    provider = CapturingProvider(
        {
            "type": "clarification",
            "message_zh": "我是学校数据同步助手，可以帮助核对和治理组织数据。",
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(message="你是谁", available_source_refs=())
    )

    assert decision.kind == "clarification"
    assert decision.message_zh.startswith("我是学校数据同步助手")


@pytest.mark.asyncio
async def test_supervisor_ignores_known_non_executable_missing_info_hint() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "clarification",
                "message_zh": "我是学校数据同步助手，请告诉我需要同步哪些实体。",
                "missing_info": ["entity_types"],
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(message="你是谁", available_source_refs=())
    )

    assert decision.kind == "clarification"

"""Model-backed supervisor for the user-facing synchronization conversation."""

from typing import Any

from pydantic import ValidationError

from app.ai.agent_analysis_service import SingleAttemptModelProvider
from app.ai.agent_prompting import build_agent_request
from app.ai.skills.registry import SkillRegistry
from app.schemas.agent_conversation import (
    ConversationAgentContext,
    ConversationAgentDecision,
)


class ConversationModelResponseError(ValueError):
    """The provider response did not satisfy the public conversation contract."""


class ConversationSupervisorAgent:
    def __init__(
        self,
        provider: SingleAttemptModelProvider,
        skills: SkillRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._skills = skills or SkillRegistry()

    async def reply(self, context: ConversationAgentContext) -> ConversationAgentDecision:
        skill = self._skills.load("converse-school-data-sync", "1.0.0")
        response = await self._provider.complete_json_once(
            build_agent_request(
                skill,
                context.model_dump(mode="json"),
                ConversationAgentDecision,
            )
        )
        decision = _parse_decision(response.output)
        return _validate_source_references(decision, context)


def _parse_decision(output: dict[str, Any]) -> ConversationAgentDecision:
    payload = output.get("result", output)
    if not isinstance(payload, dict):
        raise ConversationModelResponseError("conversation model result must be an object")
    normalized = dict(payload)
    if "kind" not in normalized and "type" in normalized:
        normalized["kind"] = normalized.pop("type")
    # Some JSON-object providers add this non-executable clarification hint
    # despite the response schema. The public message already carries the
    # clarification, so discard only this explicitly known compatibility key.
    normalized.pop("missing_info", None)
    try:
        return ConversationAgentDecision.model_validate(normalized)
    except ValidationError as error:
        raise ConversationModelResponseError(
            "conversation model result failed validation"
        ) from error


def _validate_source_references(
    decision: ConversationAgentDecision,
    context: ConversationAgentContext,
) -> ConversationAgentDecision:
    references = {value for value in context.available_source_refs}
    selected = {value for value in (decision.source_ref, decision.target_ref) if value}
    if selected <= references:
        return decision
    return ConversationAgentDecision(
        kind="clarification",
        message_zh="可用本地数据来源已变化，请从服务端列出的来源中重新确认。",
    )

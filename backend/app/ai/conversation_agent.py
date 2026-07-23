"""Model-backed supervisor for the user-facing synchronization conversation."""

from app.ai.agent_analysis_service import SingleAttemptModelProvider
from app.ai.agent_prompting import build_agent_request
from app.ai.skills.registry import SkillRegistry
from app.schemas.agent_conversation import (
    ConversationAgentContext,
    ConversationAgentDecision,
)


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
        payload = response.output.get("result")
        decision = ConversationAgentDecision.model_validate(payload)
        return _validate_source_references(decision, context)


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

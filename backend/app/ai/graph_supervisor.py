from dataclasses import dataclass

from pydantic import ValidationError

from app.agent_graph.actions import (
    InvalidSupervisorDecision,
    validate_supervisor_decision,
)
from app.agent_graph.contracts import SupervisorContextV1, SupervisorDecisionV1
from app.ai.agent_analysis_service import SingleAttemptModelProvider
from app.ai.agent_prompting import build_agent_request
from app.ai.providers.base import LLMResponse, ModelProviderError
from app.ai.skills.registry import SkillDefinition, SkillRegistry, UnsafeSkillError


class GraphSupervisorFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class GraphSupervisorCallResult:
    decision: SupervisorDecisionV1
    provider: str
    model: str
    request_id: str | None
    attempt_count: int


class GraphSupervisorAgent:
    def __init__(
        self,
        provider: SingleAttemptModelProvider,
        skills: SkillRegistry | None = None,
        *,
        max_retries: int = 3,
    ) -> None:
        if max_retries < 0 or max_retries > 3:
            raise ValueError("Supervisor max_retries must be between zero and three")
        self._provider = provider
        self._skills = skills or SkillRegistry()
        self._max_retries = max_retries

    async def decide(self, context: SupervisorContextV1) -> SupervisorDecisionV1:
        return (await self.decide_with_provenance(context)).decision

    async def decide_with_provenance(
        self,
        context: SupervisorContextV1,
    ) -> GraphSupervisorCallResult:
        skill = self._skills.load("orchestrate-controlled-agent-graph", "1.0.0")
        try:
            self._skills.validate_input(skill, context.model_dump(mode="json"))
        except (ValidationError, UnsafeSkillError) as error:
            raise GraphSupervisorFailure("invalid Supervisor context") from error
        request = build_agent_request(
            skill,
            context.model_dump(mode="json"),
            SupervisorDecisionV1,
        )
        last_error: Exception | None = None
        invalid_decision = False
        total_attempts = self._max_retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                response = await self._provider.complete_json_once(request)
                decision = self._decision_from_response(skill, response)
                validate_supervisor_decision(context, decision)
                return GraphSupervisorCallResult(
                    decision=decision,
                    provider=response.provider,
                    model=response.model,
                    request_id=response.request_id,
                    attempt_count=attempt,
                )
            except ModelProviderError as error:
                last_error = error
            except (ValidationError, UnsafeSkillError, InvalidSupervisorDecision) as error:
                invalid_decision = True
                last_error = error
        label = "invalid Supervisor decision" if invalid_decision else "Supervisor model failure"
        raise GraphSupervisorFailure(
            f"{label} after {total_attempts} attempts"
        ) from last_error

    def _decision_from_response(
        self,
        skill: SkillDefinition,
        response: LLMResponse,
    ) -> SupervisorDecisionV1:
        validated = self._skills.validate_output(
            skill,
            response.output.get("result"),
        )
        if not isinstance(validated, SupervisorDecisionV1):
            raise UnsafeSkillError("orchestrate-controlled-agent-graph")
        return validated

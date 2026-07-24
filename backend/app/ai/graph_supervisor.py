from dataclasses import dataclass

from pydantic import ValidationError

from app.agent_graph.actions import (
    InvalidSupervisorDecision,
    validate_supervisor_decision,
)
from app.agent_graph.contracts import SupervisorContextV1, SupervisorDecisionV1
from app.ai.agent_analysis_service import SingleAttemptModelProvider
from app.ai.agent_prompting import (
    build_agent_request,
    build_json_repair_request,
    extract_model_result,
)
from app.ai.providers.base import LLMResponse, ModelProviderError
from app.ai.skills.registry import SkillDefinition, SkillRegistry, UnsafeSkillError


class GraphSupervisorFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failure_categories: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.failure_categories = failure_categories


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
            response_example=_supervisor_response_example(context),
        )
        last_error: Exception | None = None
        invalid_decision = False
        failure_categories: list[str] = []
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
                failure_categories.append("model_provider_failure")
            except (
                ValidationError,
                UnsafeSkillError,
                InvalidSupervisorDecision,
                ValueError,
            ) as error:
                invalid_decision = True
                last_error = error
                failure_categories.append(_safe_supervisor_failure_category(error))
                request = build_json_repair_request(request, response.output, error)
        label = "invalid Supervisor decision" if invalid_decision else "Supervisor model failure"
        raise GraphSupervisorFailure(
            f"{label} after {total_attempts} attempts",
            failure_categories=tuple(failure_categories),
        ) from last_error

    def _decision_from_response(
        self,
        skill: SkillDefinition,
        response: LLMResponse,
    ) -> SupervisorDecisionV1:
        validated = self._skills.validate_output(
            skill,
            extract_model_result(response.output),
        )
        if not isinstance(validated, SupervisorDecisionV1):
            raise UnsafeSkillError("orchestrate-controlled-agent-graph")
        return validated


def _supervisor_response_example(context: SupervisorContextV1) -> dict[str, object]:
    selected = context.allowed_actions[0]
    alternatives = [
        {
            "action_id": action.action_id,
            "reason_zh": "本轮先处理所选安全动作，后续由服务端重新计算候选。",
        }
        for action in context.allowed_actions[1:]
    ]
    return {
        "result": {
            "action_id": selected.action_id,
            "reason_zh": "根据当前服务端证据选择该安全动作。",
            "expected_result": selected.required_evidence[0],
            "observed_blockers": list(context.active_blockers),
            "risk_notes_zh": ["风险等级沿用服务端候选定义，不自行降级。"],
            "why_not_other_actions_zh": alternatives,
            "operator_message_zh": "正在执行当前安全步骤。",
        }
    }


def _safe_supervisor_failure_category(error: Exception) -> str:
    if isinstance(error, InvalidSupervisorDecision):
        return "model_decision_failure"
    return "model_contract_failure"

from app.ai.prompting import build_messages, response_schema
from app.ai.providers.base import LLMProvider, LLMRequest, ModelProviderError
from app.ai.skills.registry import SkillRegistry
from app.ai.tokenization import TaskTokenizationContext
from app.schemas.executions import (
    GovernancePlan,
    PlanExplanation,
    PlanExplanationResponse,
)


class GovernancePlanExplainer:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        tokenization_secret: str | None,
        skills: SkillRegistry | None = None,
    ) -> None:
        self.provider = provider
        self.tokenization_secret = tokenization_secret
        self.skills = skills or SkillRegistry()

    async def explain(
        self,
        plan: GovernancePlan,
        *,
        tenant_id: str,
    ) -> PlanExplanationResponse:
        if self.tokenization_secret is None:
            raise ModelProviderError("plan explanation tokenization is not configured")
        skill = self.skills.load("generate-governance-plan", "1.0.0")
        tokenizer = TaskTokenizationContext(
            secret=self.tokenization_secret,
            tenant_id=tenant_id,
            task_id=plan.task_id,
        )
        safe_plan = tokenizer.tokenize(plan.model_dump(mode="json"))
        response = await self.provider.complete_json(
            LLMRequest(
                messages=tuple(build_messages(skill, safe_plan)),
                response_schema=response_schema(
                    skill,
                    PlanExplanation.model_json_schema(),
                ),
            )
        )
        result = response.output.get("result")
        if not isinstance(result, dict):
            raise ModelProviderError("plan explanation response is missing a result")
        explanation = PlanExplanation.model_validate(tokenizer.detokenize(result))
        return PlanExplanationResponse(
            explanation=explanation,
            provider=response.provider,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            request_id=response.request_id,
        )

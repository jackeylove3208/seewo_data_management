from uuid import UUID

from app.ai.prompting import build_messages, response_schema
from app.ai.providers.base import LLMProvider, LLMRequest, ModelProviderError
from app.ai.skills.registry import SkillRegistry
from app.ai.tokenization import TaskTokenizationContext
from app.restores.planner import RestorePlanResult
from app.schemas.reporting import RestoreAdvice


class RestoreAdvisor:
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

    async def advise(
        self,
        plan: RestorePlanResult,
        *,
        task_id: UUID,
        tenant_id: str,
    ) -> tuple[RestoreAdvice, dict[str, object]]:
        if self.tokenization_secret is None:
            raise ModelProviderError("restore tokenization is not configured")
        skill = self.skills.load("assess-rollback-impact", "1.0.0")
        tokenizer = TaskTokenizationContext(
            secret=self.tokenization_secret,
            tenant_id=tenant_id,
            task_id=task_id,
        )
        payload = tokenizer.tokenize(
            {
                "source_version_id": str(plan.source_version_id),
                "target_version_id": str(plan.target_version_id),
                "operations": [item.model_dump(mode="json") for item in plan.operations],
            }
        )
        response = await self.provider.complete_json(
            LLMRequest(
                messages=tuple(build_messages(skill, payload)),
                response_schema=response_schema(skill, RestoreAdvice.model_json_schema()),
            )
        )
        raw = response.output.get("result")
        if not isinstance(raw, dict):
            raise ModelProviderError("restore advice response is missing a result")
        advice = RestoreAdvice.model_validate(tokenizer.detokenize(raw))
        expected = tuple(item.compensation_for for item in plan.operations)
        if advice.operation_refs != expected:
            raise ModelProviderError("AI restore candidate does not match deterministic plan")
        return advice, {
            "mode": "ai",
            "provider": response.provider,
            "model": response.model,
            "skill_name": skill.name,
            "skill_version": skill.version,
            "request_id": response.request_id,
        }

from app.ai.prompting import build_messages, response_schema
from app.ai.providers.base import LLMProvider, LLMRequest, ModelProviderError
from app.ai.skills.registry import SkillRegistry
from app.ai.tokenization import TaskTokenizationContext
from app.schemas.reporting import ExecutionFactBundle, GovernanceReportContent


class ReportNarrativeGenerator:
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

    async def generate(
        self,
        facts: ExecutionFactBundle,
        *,
        tenant_id: str,
    ) -> tuple[GovernanceReportContent, dict[str, object]]:
        if self.tokenization_secret is None:
            raise ModelProviderError("report tokenization is not configured")
        skill = self.skills.load("generate-governance-report", "1.0.0")
        tokenizer = TaskTokenizationContext(
            secret=self.tokenization_secret,
            tenant_id=tenant_id,
            task_id=facts.task_id,
        )
        safe_facts = tokenizer.tokenize(facts.model_dump(mode="json"))
        response = await self.provider.complete_json(
            LLMRequest(
                messages=tuple(build_messages(skill, safe_facts)),
                response_schema=response_schema(
                    skill,
                    GovernanceReportContent.model_json_schema(),
                ),
            )
        )
        result = response.output.get("result")
        if not isinstance(result, dict):
            raise ModelProviderError("report response is missing a result")
        content = GovernanceReportContent.model_validate(tokenizer.detokenize(result))
        if content.restore_state is not facts.restore_state:
            raise ModelProviderError("report restore state does not match execution facts")
        return content, {
            "mode": "ai",
            "provider": response.provider,
            "model": response.model,
            "skill_name": skill.name,
            "skill_version": skill.version,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "request_id": response.request_id,
        }


def deterministic_report(facts: ExecutionFactBundle) -> GovernanceReportContent:
    failed = [
        operation
        for operation in facts.operations
        if operation.get("attempts") and operation["attempts"][-1].get("status") != "succeeded"
    ]
    return GovernanceReportContent(
        summary=f"执行批次 {facts.execution_id} 状态为 {facts.status}。",
        actions=tuple(
            f"{operation.get('operation_type')}:{operation.get('target_source_identifier') or '-'}"
            for operation in facts.operations
        ),
        outcomes=(f"成功或已记录操作共 {len(facts.operations) - len(failed)} 项",),
        failures=tuple(f"操作 {operation.get('operation_id')} 未成功" for operation in failed),
        restore_state=facts.restore_state,
    )

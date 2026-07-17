import re
from collections import Counter
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import (
    AgentFailure,
    AgentRequest,
    AgentResult,
)
from app.ai.analysis_policy import AnalysisPolicyError, validate_analysis_options
from app.ai.deterministic_analysis import DeterministicAnalysis
from app.ai.mcp.authorization import ToolAuthorizationError, ToolContext
from app.ai.providers.base import ModelProviderError, ModelUsage
from app.ai.skills.registry import SkillNotFound
from app.core.security import OperatorContext
from app.repositories.analyses import CURRENT_ANALYSIS_VERSION, AnalysisRepository
from app.repositories.differences import DifferenceRepository
from app.repositories.tasks import TaskRepository
from app.schemas.governance import (
    AnalysisBatchResponse,
    AnalysisJobResponse,
    AnalysisProvenance,
    AnalysisResult,
    AnalysisStatus,
    CauseAnalysisV2,
)


class AnalysisAgent(Protocol):
    async def analyze(self, request: AgentRequest) -> AgentResult: ...


ANALYSIS_FAILURES = (
    ValidationError,
    AnalysisPolicyError,
    ModelProviderError,
    AgentFailure,
    ToolAuthorizationError,
    SkillNotFound,
)


class AnalysisService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        agent: AnalysisAgent,
        operator: OperatorContext | None = None,
        differences: DifferenceRepository | None = None,
        analyses: AnalysisRepository | None = None,
        deterministic: DeterministicAnalysis | None = None,
    ) -> None:
        self.session = session
        self.agent = agent
        self.operator = operator or OperatorContext(
            operator_id="demo-operator",
            tenant_id="school-1",
        )
        self.differences = differences or DifferenceRepository(session)
        self.analyses = analyses or AnalysisRepository(session)
        self.deterministic = deterministic or DeterministicAnalysis()
        self.tasks = TaskRepository(session)

    async def analyze(self, difference_id: UUID) -> AnalysisResult:
        difference = await self.differences.get(difference_id)
        if difference is None or difference.tenant_id != self.operator.tenant_id:
            raise LookupError(f"difference not found: {difference_id}")
        existing = await self.analyses.get_for_difference(
            difference.id,
            difference.version,
            CURRENT_ANALYSIS_VERSION,
        )
        if existing is not None:
            return existing

        deterministic_output = self.deterministic.for_difference(difference)
        if deterministic_output is not None:
            validate_analysis_options(difference, deterministic_output)
            save = (
                self.analyses.save_manual_review
                if deterministic_output.manual_only
                else self.analyses.save_success
            )
            return await save(
                difference,
                deterministic_output,
                _deterministic_provenance(),
                attempt_count=0,
                analysis_version=CURRENT_ANALYSIS_VERSION,
            )

        last_error: Exception | None = None
        aggregate_provenance: AnalysisProvenance | None = None
        for attempt in range(1, 3):
            try:
                result = await self.agent.analyze(
                    AgentRequest(
                        skill_name="analyze-data-difference",
                        skill_version="1.0.0",
                        input_payload=difference.model_dump(mode="json"),
                        tool_context=ToolContext(
                            operator_id=self.operator.operator_id,
                            tenant_id=self.operator.tenant_id,
                            task_id=difference.task_id,
                            allowed_difference_ids=frozenset({difference.id}),
                        ),
                        analysis_version=CURRENT_ANALYSIS_VERSION,
                    )
                )
                aggregate_provenance = _merge_provenance(aggregate_provenance, result.provenance)
                if not isinstance(result.output, CauseAnalysisV2):
                    raise AnalysisPolicyError("analysis-v2 requires CauseAnalysisV2 output")
                validate_analysis_options(difference, result.output)
                if result.output.manual_only:
                    return await self.analyses.save_manual_review(
                        difference,
                        result.output,
                        aggregate_provenance,
                        attempt_count=attempt,
                        analysis_version=CURRENT_ANALYSIS_VERSION,
                    )
                return await self.analyses.save_success(
                    difference,
                    result.output,
                    aggregate_provenance,
                    attempt_count=attempt,
                    analysis_version=CURRENT_ANALYSIS_VERSION,
                )
            except ANALYSIS_FAILURES as error:
                last_error = error
                if isinstance(error, AgentFailure):
                    aggregate_provenance = _merge_provenance(aggregate_provenance, error.provenance)

        failure_code = _error_code(last_error)
        return await self.analyses.save_manual_review(
            difference,
            _manual_review_fallback(failure_code),
            aggregate_provenance or _failure_provenance(),
            attempt_count=2,
            failure_code=failure_code,
            analysis_version=CURRENT_ANALYSIS_VERSION,
        )

    async def analyze_task(self, task_id: UUID) -> AnalysisJobResponse:
        task = await self.tasks.get(task_id)
        if task is None or task.tenant_id != self.operator.tenant_id:
            raise LookupError(f"reconciliation task not found: {task_id}")
        differences = await self.differences.for_task(task_id)
        result = await self.analyze_batch(task_id, limit=max(1, len(differences)))
        return AnalysisJobResponse(
            task_id=task_id,
            total=result.total,
            succeeded=result.succeeded,
            failed=result.failed,
            manual_review=result.manual_review,
        )

    async def analyze_batch(self, task_id: UUID, *, limit: int) -> AnalysisBatchResponse:
        task = await self.tasks.get(task_id)
        if task is None or task.tenant_id != self.operator.tenant_id:
            raise LookupError(f"reconciliation task not found: {task_id}")
        if limit < 1:
            raise ValueError("analysis batch limit must be positive")
        differences = await self.differences.for_task(task_id)
        pending = []
        for difference in differences:
            existing = await self.analyses.get_for_difference(
                difference.id,
                difference.version,
                CURRENT_ANALYSIS_VERSION,
            )
            if existing is None:
                pending.append(difference)
        for difference in pending[:limit]:
            await self.analyze(difference.id)
        results = [
            result
            for difference in differences
            if (
                result := await self.analyses.get_for_difference(
                    difference.id,
                    difference.version,
                    CURRENT_ANALYSIS_VERSION,
                )
            )
            is not None
        ]
        counts = Counter(result.status for result in results)
        completed = len(results)
        return AnalysisBatchResponse(
            task_id=task_id,
            total=len(differences),
            succeeded=counts[AnalysisStatus.SUCCEEDED],
            failed=counts[AnalysisStatus.FAILED],
            manual_review=counts[AnalysisStatus.MANUAL_REVIEW],
            completed=completed,
            remaining=len(differences) - completed,
        )


def _deterministic_provenance() -> AnalysisProvenance:
    return AnalysisProvenance(
        provider="deterministic",
        model="deterministic-analysis-v2",
        skill_name="analyze-data-difference",
        skill_version="1.0.0",
        prompt_version="analysis-prompt-v2",
        usage=ModelUsage(),
        generated_at=datetime.now(UTC),
    )


def _failure_provenance() -> AnalysisProvenance:
    return AnalysisProvenance(
        provider="unavailable",
        model="unavailable",
        skill_name="analyze-data-difference",
        skill_version="1.0.0",
        prompt_version="analysis-prompt-v2",
        usage=ModelUsage(),
        generated_at=datetime.now(UTC),
    )


def _manual_review_fallback(failure_code: str) -> CauseAnalysisV2:
    return CauseAnalysisV2(
        cause="Automatic analysis did not produce a policy-compliant recommendation",
        evidence_summary=(f"Analysis stopped after 2 attempts with failure code: {failure_code}"),
        manual_only=True,
        manual_reason="A human must review the evidence and author an explicit proposal",
    )


def _error_code(error: Exception | None) -> str:
    if error is None:
        return "unknown_analysis_error"
    if isinstance(error, AgentFailure) and error.cause is not None:
        error = error.cause
    name = type(error).__name__
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _merge_provenance(
    current: AnalysisProvenance | None,
    attempt: AnalysisProvenance,
) -> AnalysisProvenance:
    if current is None:
        return attempt
    provider = attempt.provider if attempt.provider != "unavailable" else current.provider
    model = attempt.model if attempt.model != "unavailable" else current.model
    return AnalysisProvenance(
        provider=provider,
        model=model,
        skill_name=attempt.skill_name,
        skill_version=attempt.skill_version,
        prompt_version=attempt.prompt_version,
        tool_trace_ids=tuple(dict.fromkeys((*current.tool_trace_ids, *attempt.tool_trace_ids))),
        gateway_request_ids=tuple(
            dict.fromkeys((*current.gateway_request_ids, *attempt.gateway_request_ids))
        ),
        usage=ModelUsage(
            input_tokens=(current.usage.input_tokens + attempt.usage.input_tokens),
            output_tokens=(current.usage.output_tokens + attempt.usage.output_tokens),
        ),
        generated_at=max(current.generated_at, attempt.generated_at),
    )

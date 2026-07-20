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
from app.ai.analysis_policy import (
    AnalysisPolicyError,
    validate_analysis_options,
    validate_analysis_v3,
)
from app.ai.deterministic_analysis import DeterministicAnalysis
from app.ai.mcp.authorization import ToolAuthorizationError, ToolContext
from app.ai.providers.base import ModelProviderError, ModelUsage, TransientModelError
from app.ai.skills.registry import SkillNotFound
from app.core.security import OperatorContext
from app.repositories.analyses import (
    ANALYSIS_V3_VERSION,
    CURRENT_ANALYSIS_VERSION,
    AnalysisRepository,
)
from app.repositories.differences import DifferenceRepository
from app.repositories.tasks import TaskRepository
from app.schemas.governance import (
    AnalysisBatchResponse,
    AnalysisJobResponse,
    AnalysisProvenance,
    AnalysisResult,
    AnalysisStatus,
    AutoExecutableResolution,
    CauseAnalysisV2,
    CauseAnalysisV3,
    ManualResolution,
    ManualStep,
    RiskLevel,
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


class AnalysisExecutionError(RuntimeError):
    def __init__(
        self,
        failure_code: str,
        *,
        transient: bool,
        provenance: AnalysisProvenance,
    ) -> None:
        super().__init__(failure_code)
        self.failure_code = failure_code
        self.transient = transient
        self.provenance = provenance


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

    async def analyze_v3(
        self,
        difference_id: UUID,
        *,
        fallback_on_failure: bool = True,
    ) -> AnalysisResult:
        difference = await self.differences.get(difference_id)
        if difference is None or difference.tenant_id != self.operator.tenant_id:
            raise LookupError(f"difference not found: {difference_id}")
        existing = await self.analyses.get_for_difference(
            difference.id,
            difference.version,
            ANALYSIS_V3_VERSION,
        )
        if existing is not None:
            return existing

        # The model gateway must never inherit the read transaction used to load evidence.
        await self.session.commit()

        deterministic_output = self.deterministic.for_difference_v3(difference)
        if deterministic_output is not None:
            validate_analysis_v3(difference, deterministic_output)
            save = (
                self.analyses.save_success
                if _has_executable_resolution(deterministic_output)
                else self.analyses.save_manual_review
            )
            return await save(
                difference,
                deterministic_output,
                _deterministic_provenance_v3(),
                attempt_count=0,
                analysis_version=ANALYSIS_V3_VERSION,
            )

        last_error: Exception | None = None
        aggregate_provenance: AnalysisProvenance | None = None
        for attempt in range(1, 3):
            input_payload = difference.model_dump(mode="json")
            if last_error is not None:
                input_payload["validation_feedback"] = _error_code(last_error)
            try:
                result = await self.agent.analyze(
                    AgentRequest(
                        skill_name="analyze-data-difference",
                        skill_version="1.0.0",
                        input_payload=input_payload,
                        tool_context=ToolContext(
                            operator_id=self.operator.operator_id,
                            tenant_id=self.operator.tenant_id,
                            task_id=difference.task_id,
                            allowed_difference_ids=frozenset({difference.id}),
                        ),
                        analysis_version=ANALYSIS_V3_VERSION,
                    )
                )
                aggregate_provenance = _merge_provenance(
                    aggregate_provenance,
                    result.provenance,
                )
                if not isinstance(result.output, CauseAnalysisV3):
                    raise AnalysisPolicyError("analysis-v3 requires CauseAnalysisV3 output")
                validate_analysis_v3(difference, result.output)
                save = (
                    self.analyses.save_success
                    if _has_executable_resolution(result.output)
                    else self.analyses.save_manual_review
                )
                return await save(
                    difference,
                    result.output,
                    aggregate_provenance,
                    attempt_count=attempt,
                    analysis_version=ANALYSIS_V3_VERSION,
                )
            except ANALYSIS_FAILURES as error:
                last_error = error
                if isinstance(error, AgentFailure):
                    aggregate_provenance = _merge_provenance(
                        aggregate_provenance,
                        error.provenance,
                    )

        failure_code = _error_code(last_error)
        if not fallback_on_failure:
            raise AnalysisExecutionError(
                failure_code,
                transient=_is_transient_failure(last_error),
                provenance=aggregate_provenance or _failure_provenance_v3(),
            )
        return await self.analyses.save_manual_review(
            difference,
            _manual_review_fallback_v3(difference.difference_type.value),
            aggregate_provenance or _failure_provenance_v3(),
            attempt_count=2,
            failure_code=failure_code,
            analysis_version=ANALYSIS_V3_VERSION,
        )

    async def persist_v3_fallback(
        self,
        difference_id: UUID,
        *,
        failure_code: str,
        attempt_count: int,
        provenance: AnalysisProvenance | None = None,
    ) -> AnalysisResult:
        difference = await self.differences.get(difference_id)
        if difference is None or difference.tenant_id != self.operator.tenant_id:
            raise LookupError(f"difference not found: {difference_id}")
        existing = await self.analyses.get_for_difference(
            difference.id,
            difference.version,
            ANALYSIS_V3_VERSION,
        )
        if existing is not None:
            return existing
        return await self.analyses.save_manual_review(
            difference,
            _manual_review_fallback_v3(difference.difference_type.value),
            provenance or _failure_provenance_v3(),
            attempt_count=attempt_count,
            failure_code=failure_code,
            analysis_version=ANALYSIS_V3_VERSION,
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


def _deterministic_provenance_v3() -> AnalysisProvenance:
    return AnalysisProvenance(
        provider="deterministic",
        model="deterministic-analysis-v3",
        skill_name="analyze-data-difference",
        skill_version="1.0.0",
        prompt_version="analysis-prompt-v3",
        usage=ModelUsage(),
        generated_at=datetime.now(UTC),
    )


def _failure_provenance_v3() -> AnalysisProvenance:
    return AnalysisProvenance(
        provider="unavailable",
        model="unavailable",
        skill_name="analyze-data-difference",
        skill_version="1.0.0",
        prompt_version="analysis-prompt-v3",
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


def _manual_review_fallback_v3(difference_type: str) -> CauseAnalysisV3:
    solution_id = "manual-safe-fallback"
    difference_label = {
        "seewo_missing": "希沃缺失",
        "seewo_redundant": "希沃多余",
        "attribute_conflict": "属性不一致",
        "structure_conflict": "归属不一致",
        "duplicate_conflict": "重复记录冲突",
    }.get(difference_type, "当前数据差异")
    return CauseAnalysisV3(
        locale="zh-CN",
        issue_title="需要人工核对数据差异",
        cause_summary="自动分析暂时无法形成符合安全规则的修改建议。",
        evidence_summary="系统已保留当前差异和双方快照证据，未执行任何数据修改。",
        business_impact="在证据确认前继续自动处理可能修改错误的组织或人员记录。",
        recommended_solution_id=solution_id,
        solutions=(
            ManualResolution(
                solution_id=solution_id,
                title="人工核对并生成方案",
                rationale="请根据当前差异类型和权威快照确认正确处理方式。",
                risk=RiskLevel.HIGH,
                risk_reason="自动分析失败时不能安全推断目标修改。",
                confidence=0,
                recommended=True,
                manual_steps=(
                    ManualStep(order=1, instruction="核对第三方权威记录与希沃当前记录。"),
                    ManualStep(
                        order=2,
                        instruction=f"确认{difference_label}对应的真实业务原因。",
                    ),
                    ManualStep(order=3, instruction="通过人工编辑器生成待执行治理方案。"),
                ),
            ),
        ),
    )


def _has_executable_resolution(analysis: CauseAnalysisV3) -> bool:
    return any(isinstance(solution, AutoExecutableResolution) for solution in analysis.solutions)


def _error_code(error: Exception | None) -> str:
    if error is None:
        return "unknown_analysis_error"
    if isinstance(error, AgentFailure) and error.cause is not None:
        error = error.cause
    name = type(error).__name__
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _is_transient_failure(error: Exception | None) -> bool:
    if isinstance(error, AgentFailure) and error.cause is not None:
        error = error.cause
    return isinstance(error, TransientModelError)


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

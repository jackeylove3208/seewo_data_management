from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ai.providers.base import ModelUsage


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RecommendedAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    MOVE = "move"
    DISABLE = "disable"
    SKIP = "skip"
    MANUAL_REVIEW = "manual_review"


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"


class CauseAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cause: str = Field(min_length=3, max_length=1000)
    evidence_summary: str = Field(min_length=3, max_length=2000)
    recommended_action: RecommendedAction
    risk: RiskLevel
    confidence: float = Field(ge=0, le=1)

    @field_validator("cause", "evidence_summary")
    @classmethod
    def reject_blank_explanation(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 3:
            raise ValueError("analysis explanation must contain at least 3 characters")
        return stripped


class ProposedFieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1, max_length=128)
    before: Any = None
    after: Any = None


class GovernanceOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    option_id: str = Field(min_length=1, max_length=64)
    operation_type: RecommendedAction
    target_entity_id: UUID | None = None
    proposed_changes: tuple[ProposedFieldChange, ...] = Field(default=(), max_length=64)
    rationale: str = Field(min_length=3, max_length=2000)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    risk: RiskLevel
    confidence: float = Field(ge=0, le=1)
    preconditions: tuple[str, ...] = Field(default=(), max_length=32)
    recommended: bool = False

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 3:
            raise ValueError("option rationale must contain at least 3 characters")
        return stripped

    @model_validator(mode="after")
    def reject_manual_review(self) -> "GovernanceOption":
        if self.operation_type is RecommendedAction.MANUAL_REVIEW:
            raise ValueError("manual review is not an executable option")
        return self


class ResolutionMode(StrEnum):
    AUTO_EXECUTABLE = "auto_executable"
    NEEDS_INFORMATION = "needs_information"
    MANUAL_ONLY = "manual_only"


class ResolutionAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_type: RecommendedAction
    target_entity_id: UUID | None = None
    proposed_changes: tuple[ProposedFieldChange, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def reject_manual_review(self) -> "ResolutionAction":
        if self.operation_type is RecommendedAction.MANUAL_REVIEW:
            raise ValueError("manual review is not an executable action")
        return self


class InformationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_type: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=3, max_length=500)
    reason: str = Field(min_length=3, max_length=1000)
    source_hint: str = Field(min_length=2, max_length=500)


class ManualStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order: int = Field(ge=1, le=20)
    instruction: str = Field(min_length=3, max_length=1000)


class ResolutionPathBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    solution_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=3, max_length=200)
    rationale: str = Field(min_length=3, max_length=2000)
    risk: RiskLevel
    risk_reason: str = Field(min_length=3, max_length=1000)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    preconditions: tuple[str, ...] = Field(default=(), max_length=32)
    recommended: bool = False


class AutoExecutableResolution(ResolutionPathBase):
    mode: Literal[ResolutionMode.AUTO_EXECUTABLE] = ResolutionMode.AUTO_EXECUTABLE
    action: ResolutionAction


class NeedsInformationResolution(ResolutionPathBase):
    mode: Literal[ResolutionMode.NEEDS_INFORMATION] = ResolutionMode.NEEDS_INFORMATION
    information_requests: tuple[InformationRequest, ...] = Field(min_length=1, max_length=10)


class ManualResolution(ResolutionPathBase):
    mode: Literal[ResolutionMode.MANUAL_ONLY] = ResolutionMode.MANUAL_ONLY
    manual_steps: tuple[ManualStep, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_step_order(self) -> "ManualResolution":
        orders = tuple(step.order for step in self.manual_steps)
        if orders != tuple(range(1, len(orders) + 1)):
            raise ValueError("manual steps must be ordered from 1 without gaps")
        return self


ResolutionPath = Annotated[
    AutoExecutableResolution | NeedsInformationResolution | ManualResolution,
    Field(discriminator="mode"),
]


class CauseAnalysisV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    locale: Literal["zh-CN"] = "zh-CN"
    issue_title: str = Field(min_length=3, max_length=200)
    cause_summary: str = Field(min_length=3, max_length=1000)
    evidence_summary: str = Field(min_length=3, max_length=2000)
    business_impact: str = Field(min_length=3, max_length=1000)
    recommended_solution_id: str = Field(min_length=1, max_length=64)
    solutions: tuple[ResolutionPath, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_recommendation(self) -> "CauseAnalysisV3":
        identifiers = tuple(solution.solution_id for solution in self.solutions)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("solution ids must be unique")
        recommended = tuple(solution for solution in self.solutions if solution.recommended)
        if len(recommended) != 1:
            raise ValueError("exactly one solution must be recommended")
        if recommended[0].solution_id != self.recommended_solution_id:
            raise ValueError("recommended solution id must reference the recommended solution")
        return self


class CauseAnalysisV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cause: str = Field(min_length=3, max_length=1000)
    evidence_summary: str = Field(min_length=3, max_length=2000)
    manual_only: bool
    manual_reason: str | None = Field(default=None, max_length=2000)
    options: tuple[GovernanceOption, ...] = Field(default=(), max_length=3)

    @field_validator("cause", "evidence_summary")
    @classmethod
    def reject_blank_analysis_text(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 3:
            raise ValueError("analysis explanation must contain at least 3 characters")
        return stripped

    @field_validator("manual_reason")
    @classmethod
    def normalize_manual_reason(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_option_mode(self) -> "CauseAnalysisV2":
        if self.manual_only:
            if self.options:
                raise ValueError("manual-only analysis cannot contain options")
            if self.manual_reason is None or len(self.manual_reason) < 3:
                raise ValueError("manual reason is required for manual-only analysis")
            return self
        if not self.options:
            raise ValueError("non-manual analysis requires at least one option")
        if sum(option.recommended for option in self.options) != 1:
            raise ValueError("exactly one option must be recommended")
        if self.manual_reason is not None:
            raise ValueError("manual reason is only valid for manual-only analysis")
        return self


class AnalysisProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=255)
    skill_name: str = Field(min_length=1, max_length=128)
    skill_version: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=64)
    tool_trace_ids: tuple[str, ...] = ()
    gateway_request_ids: tuple[str, ...] = ()
    usage: ModelUsage = Field(default_factory=ModelUsage)
    generated_at: datetime


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    difference_id: UUID
    difference_version: int = Field(ge=1)
    analysis_version: str = Field(min_length=1, max_length=64)
    status: AnalysisStatus
    output: CauseAnalysis | CauseAnalysisV2 | CauseAnalysisV3 | None = None
    failure_code: str | None = Field(default=None, max_length=128)
    attempt_count: int = Field(ge=0)
    provenance: AnalysisProvenance

    @model_validator(mode="after")
    def validate_status_output(self) -> "AnalysisResult":
        if self.analysis_version == "analysis-v3":
            if not isinstance(self.output, CauseAnalysisV3):
                if self.status in {AnalysisStatus.PENDING, AnalysisStatus.FAILED}:
                    if self.output is not None:
                        raise ValueError("pending or failed analysis cannot contain output")
                    return self
                raise ValueError("terminal v3 analysis requires v3 output")
            has_executable = any(
                isinstance(solution, AutoExecutableResolution) for solution in self.output.solutions
            )
            if self.status is AnalysisStatus.SUCCEEDED and not has_executable:
                raise ValueError("succeeded v3 analysis requires an executable resolution")
            if self.status is AnalysisStatus.MANUAL_REVIEW and has_executable:
                raise ValueError("manual review v3 analysis cannot contain executable resolution")
            return self
        if self.analysis_version == "analysis-v2":
            if self.status is AnalysisStatus.SUCCEEDED:
                if not isinstance(self.output, CauseAnalysisV2) or self.output.manual_only:
                    raise ValueError("succeeded v2 analysis requires executable options")
            elif self.status is AnalysisStatus.MANUAL_REVIEW:
                if not isinstance(self.output, CauseAnalysisV2) or not self.output.manual_only:
                    raise ValueError("manual review v2 analysis requires manual-only output")
            elif self.output is not None:
                raise ValueError("pending or failed analysis cannot contain successful output")
            return self
        action = self.output.recommended_action if isinstance(self.output, CauseAnalysis) else None
        if self.status is AnalysisStatus.SUCCEEDED:
            if (
                not isinstance(self.output, CauseAnalysis)
                or action is RecommendedAction.MANUAL_REVIEW
            ):
                raise ValueError("succeeded analysis requires an executable output")
        elif self.status is AnalysisStatus.MANUAL_REVIEW:
            if (
                not isinstance(self.output, CauseAnalysis)
                or action is not RecommendedAction.MANUAL_REVIEW
            ):
                raise ValueError("manual review requires a manual-review output")
        elif self.output is not None:
            raise ValueError("pending or failed analysis cannot contain successful output")
        return self


class AnalysisJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    manual_review: int = Field(ge=0)


class AnalysisBatchResponse(AnalysisJobResponse):
    completed: int = Field(ge=0)
    remaining: int = Field(ge=0)

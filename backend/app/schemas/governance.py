from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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


class AnalysisProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=255)
    skill_name: str = Field(min_length=1, max_length=128)
    skill_version: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=64)
    tool_trace_ids: tuple[str, ...] = ()
    usage: ModelUsage = Field(default_factory=ModelUsage)
    generated_at: datetime


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    difference_id: UUID
    difference_version: int = Field(ge=1)
    analysis_version: str = Field(min_length=1, max_length=64)
    status: AnalysisStatus
    output: CauseAnalysis | None = None
    failure_code: str | None = Field(default=None, max_length=128)
    attempt_count: int = Field(ge=0)
    provenance: AnalysisProvenance

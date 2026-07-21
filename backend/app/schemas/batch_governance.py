from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.analysis_jobs import AnalysisJobStatus
from app.schemas.canonical_entities import EntityType
from app.schemas.governance import ProposedFieldChange, RecommendedAction, RiskLevel


class BatchExclusionReason(StrEnum):
    HIGH_RISK = "high_risk"
    NEEDS_INFORMATION = "needs_information"
    MANUAL_ONLY = "manual_only"
    ANALYSIS_FAILED = "analysis_failed"
    STALE = "stale"
    EXISTING_PROPOSAL = "existing_proposal"
    NO_RECOMMENDED_ACTION = "no_recommended_action"


class EntityIssueSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: EntityType
    issue_count: int = Field(ge=0)
    proposal_ready: int = Field(ge=0)
    needs_information: int = Field(ge=0)
    manual_only: int = Field(ge=0)
    failed: int = Field(ge=0)


class TaskAnalysisSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    analysis_job_id: UUID | None = None
    job_status: AnalysisJobStatus | None = None
    terminal: bool
    entity_types: tuple[EntityIssueSummary, ...]


class BatchPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_job_id: UUID
    entity_type: EntityType | None = None


class BatchPreviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    difference_id: UUID
    difference_version: int = Field(ge=1)
    analysis_id: UUID
    solution_id: str = Field(min_length=1, max_length=64)
    entity_type: EntityType
    title: str
    operation_type: RecommendedAction
    changes: tuple[ProposedFieldChange, ...]
    risk: RiskLevel


class BatchExcludedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    difference_id: UUID
    entity_type: EntityType
    reason: BatchExclusionReason
    reason_label: str


class BatchProposalPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    analysis_job_id: UUID
    preview_token: str = Field(min_length=20)
    included: tuple[BatchPreviewItem, ...]
    excluded: tuple[BatchExcludedItem, ...]


class ConfirmBatchProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preview_token: str = Field(min_length=20)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("idempotency key cannot be blank")
        return normalized


class BatchItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    difference_id: UUID
    status: str
    proposal_id: UUID | None = None
    reason: str | None = None


class BatchProposalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    created: int = Field(ge=0)
    skipped: int = Field(ge=0)
    failed: int = Field(ge=0)
    items: tuple[BatchItemResult, ...]

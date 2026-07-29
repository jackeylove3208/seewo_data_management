from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AnalysisJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    CANCELED = "canceled"


class AnalysisWorkItemStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    MANUAL_REQUIRED = "manual_required"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    CANCELED = "canceled"


class AnalysisJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("idempotency key cannot be blank")
        return normalized


class AnalysisJobProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    task_id: UUID
    status: AnalysisJobStatus
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    manual_required: int = Field(ge=0)
    needs_information: int = Field(default=0, ge=0)
    manual_only: int = Field(default=0, ge=0)
    failed: int = Field(ge=0)
    proposal_ready: int = Field(default=0, ge=0)
    last_error: str | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def validate_counts(self) -> "AnalysisJobProgress":
        if self.completed != self.succeeded + self.manual_required + self.failed:
            raise ValueError("completed count must equal terminal outcome counts")
        if self.completed > self.total:
            raise ValueError("completed count cannot exceed total")
        if self.manual_required != self.needs_information + self.manual_only:
            raise ValueError("manual-required count must equal its resolution subtotals")
        if self.proposal_ready > self.succeeded:
            raise ValueError("proposal-ready count cannot exceed succeeded count")
        return self

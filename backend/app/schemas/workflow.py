from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkflowStage(StrEnum):
    INGESTION = "ingestion"
    MATCHING = "matching"
    DIFFERENCES = "differences"
    ANALYSIS = "analysis"
    COMPLETE = "complete"


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class WorkflowError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)
    retryable: bool = False


class AnalysisProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID | None = None
    total: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    manual_review: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "AnalysisProgress":
        if self.completed != self.succeeded + self.manual_review + self.failed:
            raise ValueError("completed count must equal outcome counts")
        if self.completed > self.total:
            raise ValueError("completed count cannot exceed total")
        return self


class WorkflowState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: WorkflowStage
    status: WorkflowStatus
    attempt: int = Field(default=0, ge=0)
    processed: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    analysis: AnalysisProgress = Field(default_factory=AnalysisProgress)
    error: WorkflowError | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "WorkflowState":
        if self.processed > self.total:
            raise ValueError("processed count cannot exceed total")
        if self.status is WorkflowStatus.FAILED and self.error is None:
            raise ValueError("failed workflow state requires an error")
        if self.status is not WorkflowStatus.FAILED and self.error is not None:
            raise ValueError("only a failed workflow state may contain an error")
        return self

    @property
    def can_retry(self) -> bool:
        return bool(self.status is WorkflowStatus.FAILED and self.error and self.error.retryable)

    @property
    def can_advance(self) -> bool:
        return self.status in {WorkflowStatus.PENDING, WorkflowStatus.SUCCEEDED} and (
            self.stage is not WorkflowStage.COMPLETE
        )


class WorkflowAdvanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    workflow: WorkflowState

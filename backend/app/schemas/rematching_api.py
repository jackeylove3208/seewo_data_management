from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.rematching import MatchingQualityResult


class RematchingStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    CANCELED = "canceled"


class RematchingJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    task_id: UUID
    status: RematchingStatus
    initial_unresolved: int = Field(ge=0)
    indexed: int = Field(ge=0)
    processed: int = Field(ge=0)
    ai_recovered: int = Field(ge=0)
    no_match: int = Field(ge=0)
    manual_review: int = Field(ge=0)
    conflict: int = Field(ge=0)
    failed: int = Field(ge=0)
    updated_at: datetime


class MatchingQualityResponse(MatchingQualityResult):
    pass

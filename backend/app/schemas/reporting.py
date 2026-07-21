from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReportStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RestoreState(StrEnum):
    NOT_RESTORED = "not_restored"
    PREVIEWED = "restore_previewed"
    CONFIRMED = "restore_confirmed"
    RESTORED = "restored"
    RESTORE_EXECUTION = "restore_execution"


class ExecutionFactBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    task_id: UUID
    plan_id: UUID
    plan_version: int = Field(ge=1)
    source_snapshot_id: UUID
    target_snapshot_id: UUID
    input_target_version_id: UUID
    output_target_version_ids: tuple[UUID, ...]
    status: str
    confirmed_by: str
    confirmed_at: datetime
    operations: tuple[dict[str, Any], ...]
    analyses: tuple[dict[str, Any], ...] = ()
    difference_statistics: dict[str, int] = Field(default_factory=dict)
    failures: tuple[dict[str, Any], ...] = ()
    audit_events: tuple[dict[str, Any], ...]
    restore_state: RestoreState = RestoreState.NOT_RESTORED


class GovernanceReportContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1, max_length=4000)
    causes: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    restore_state: RestoreState


class ReportJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    execution_id: UUID
    version: int
    status: ReportStatus
    requested_by: str
    created_at: datetime
    error_code: str | None = None


class GovernanceReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    job_id: UUID
    execution_id: UUID
    version: int
    facts_hash: str
    facts: ExecutionFactBundle
    content: GovernanceReportContent
    html_hash: str
    provenance: dict[str, Any]
    generated_by: str
    generated_at: datetime


class RestoreConflict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    operation_id: UUID | None = None


class RestorePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    restore_request_id: UUID
    source_version_id: UUID
    semantic_source_version_id: UUID
    target_version_id: UUID
    preview_hash: str = Field(min_length=64, max_length=64)
    allowed: bool
    conflicts: tuple[RestoreConflict, ...]
    operations: tuple[dict[str, Any], ...]
    covered_execution_ids: tuple[UUID, ...]
    explanation: str | None = None
    explanation_state: str = "unavailable"


class ConfirmRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preview_hash: str = Field(min_length=64, max_length=64)
    high_risk_acknowledged: bool


class RestoreRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    task_id: UUID
    source_version_id: UUID
    semantic_source_version_id: UUID
    target_version_id: UUID
    preview_hash: str
    requested_by: str
    created_at: datetime


class RestoreAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_refs: tuple[UUID, ...]
    explanation: str = Field(min_length=1, max_length=4000)
    risks: tuple[str, ...] = ()


class RestoreConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    restore_request_id: UUID
    batch_id: UUID
    plan_id: UUID
    input_target_version_id: UUID
    confirmed_by: str
    status: str


class TargetVersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    parent_version_id: UUID | None
    task_id: UUID
    batch_id: UUID | None
    content_hash: str
    created_at: datetime

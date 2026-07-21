import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
    model_validator,
)

from app.schemas.canonical_entities import EntityType
from app.schemas.differences import DifferenceType
from app.schemas.governance import RiskLevel


def _freeze_fact_value(value: Any) -> Any:
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("fact numbers must be finite JSON values")
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_fact_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_fact_value(item) for item in value)
    return value


def _serialize_fact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _serialize_fact_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialize_fact_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _serialize_fact_value(value),
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def json_values_equal(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


class OperationType(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    MOVE = "move"
    DISABLE = "disable"
    SKIP = "skip"


class OperationStatus(StrEnum):
    PENDING = "pending"
    BLOCKED = "blocked"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    VERIFICATION_FAILED = "verification_failed"


class ExecutionBatchStatus(StrEnum):
    CONFIRMED = "confirmed"
    SUCCEEDED = "succeeded"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"


class ProposalSource(StrEnum):
    AI = "ai"
    OPERATOR = "operator"


class ProposalStatus(StrEnum):
    PENDING_EXECUTION = "pending_execution"
    SUPERSEDED = "superseded"
    EXECUTED = "executed"
    REJECTED = "rejected"


class ProposalVersionRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    proposal_id: UUID
    proposal_version: int = Field(ge=1)


class ReviewedProposalSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    proposal: ProposalVersionRef
    current_proposal_version: int = Field(ge=1)
    status: ProposalStatus
    task_id: UUID
    source_snapshot_id: UUID
    target_snapshot_id: UUID
    target_version: str = Field(min_length=1, max_length=128)
    proposal_source: ProposalSource
    difference_id: UUID
    difference_version: int = Field(ge=1)
    current_difference_version: int = Field(ge=1)
    analysis_id: UUID
    analysis_version: str = Field(min_length=1, max_length=64)
    current_analysis_version: str = Field(min_length=1, max_length=64)
    difference_type: DifferenceType
    operation_type: OperationType
    entity_type: EntityType
    target_entity_id: UUID | None = None
    target_source_identifier: str | None = Field(default=None, min_length=1, max_length=255)
    before: Mapping[str, JsonValue] | None = None
    after: Mapping[str, JsonValue] | None = None
    changed_fields: frozenset[str] = Field(default_factory=frozenset)
    dependencies: frozenset[UUID] = Field(default_factory=frozenset)
    reversible: bool
    risk: RiskLevel
    compensation_for: UUID | None = None
    restore_absence: bool = False

    @field_validator("before", "after", mode="after")
    @classmethod
    def freeze_facts(cls, value: Mapping[str, JsonValue] | None) -> Mapping[str, JsonValue] | None:
        if value is None:
            return None
        return cast(Mapping[str, JsonValue], _freeze_fact_value(value))

    @field_serializer("before", "after")
    def serialize_facts(self, value: Mapping[str, JsonValue] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return cast(dict[str, Any], _serialize_fact_value(value))


class GovernanceOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: UUID = Field(default_factory=uuid4)
    proposal: ProposalVersionRef
    proposal_source: ProposalSource
    difference_id: UUID
    difference_version: int = Field(ge=1)
    analysis_id: UUID
    analysis_version: str = Field(min_length=1, max_length=64)
    operation_type: OperationType
    entity_type: EntityType
    target_entity_id: UUID | None = None
    target_source_identifier: str | None = Field(default=None, min_length=1, max_length=255)
    before: Mapping[str, JsonValue] | None = None
    after: Mapping[str, JsonValue] | None = None
    changed_fields: frozenset[str] = Field(default_factory=frozenset)
    dependencies: frozenset[UUID] = Field(default_factory=frozenset)
    reversible: bool
    risk: RiskLevel
    compensation_for: UUID | None = None
    restore_absence: bool = False

    @field_validator("before", "after", mode="after")
    @classmethod
    def freeze_facts(cls, value: Mapping[str, JsonValue] | None) -> Mapping[str, JsonValue] | None:
        if value is None:
            return None
        return cast(Mapping[str, JsonValue], _freeze_fact_value(value))

    @field_serializer("before", "after")
    def serialize_facts(self, value: Mapping[str, JsonValue] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return cast(dict[str, Any], _serialize_fact_value(value))

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "GovernanceOperation":
        has_target = self.target_entity_id is not None or self.target_source_identifier is not None

        if self.operation_type is OperationType.CREATE:
            if has_target or self.before is not None:
                raise ValueError("create operations cannot reference an existing target")
            if self.after is None:
                raise ValueError("create operations require after facts")
        elif self.operation_type in {
            OperationType.UPDATE,
            OperationType.MOVE,
            OperationType.DISABLE,
        }:
            if not has_target:
                raise ValueError("target mutations require a target identifier")
            if self.before is None or self.after is None:
                raise ValueError("target mutations require expected before and after facts")
        elif self.changed_fields or (
            self.after is not None and not json_values_equal(self.after, self.before)
        ):
            raise ValueError("skip operations must be non-mutating")

        return self


class GovernancePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: UUID
    version: int = Field(default=1, ge=1)
    task_id: UUID
    source_snapshot_id: UUID
    target_snapshot_id: UUID
    target_version: str = Field(min_length=1, max_length=128)
    proposals: tuple[ProposalVersionRef, ...] = Field(min_length=1)
    operations: tuple[GovernanceOperation, ...] = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExecutionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: UUID
    plan_id: UUID
    plan_version: int = Field(ge=1)
    input_target_version_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    status: ExecutionBatchStatus = ExecutionBatchStatus.CONFIRMED
    confirmed_by: str = Field(min_length=1, max_length=128)
    independent_reviewer_id: str | None = Field(default=None, min_length=1, max_length=128)
    high_risk_acknowledged: bool = False
    preflight_result: Mapping[str, JsonValue]
    confirmed_at: datetime
    created_at: datetime

    @field_validator("preflight_result", mode="after")
    @classmethod
    def freeze_preflight(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        return cast(Mapping[str, JsonValue], _freeze_fact_value(value))

    @field_serializer("preflight_result")
    def serialize_preflight(self, value: Mapping[str, JsonValue]) -> dict[str, Any]:
        return cast(dict[str, Any], _serialize_fact_value(value))


class OperationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: UUID
    operation_id: UUID
    attempt_number: int = Field(ge=1)
    status: OperationStatus
    error_code: str | None = Field(default=None, max_length=128)
    error_detail: Mapping[str, JsonValue] | None = None
    actual_after: Mapping[str, JsonValue] | None = None
    verification: Mapping[str, JsonValue] | None = None
    retryable: bool = False
    target_version_id: UUID | None = None
    created_at: datetime

    @field_validator("error_detail", "actual_after", "verification", mode="after")
    @classmethod
    def freeze_attempt_facts(
        cls, value: Mapping[str, JsonValue] | None
    ) -> Mapping[str, JsonValue] | None:
        if value is None:
            return None
        return cast(Mapping[str, JsonValue], _freeze_fact_value(value))

    @field_serializer("error_detail", "actual_after", "verification")
    def serialize_attempt_facts(
        self, value: Mapping[str, JsonValue] | None
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return cast(dict[str, Any], _serialize_fact_value(value))


class TargetVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: UUID
    parent_version_id: UUID | None = None
    task_id: UUID
    tenant_id: str = Field(min_length=1, max_length=128)
    source_snapshot_id: UUID
    batch_id: UUID | None = None
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage_path: str = Field(min_length=1, max_length=1024)
    created_at: datetime


class ExecutionAuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: UUID
    batch_id: UUID
    operation_id: UUID | None = None
    actor_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=64)
    details: Mapping[str, JsonValue]
    created_at: datetime

    @field_validator("details", mode="after")
    @classmethod
    def freeze_details(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        return cast(Mapping[str, JsonValue], _freeze_fact_value(value))

    @field_serializer("details")
    def serialize_details(self, value: Mapping[str, JsonValue]) -> dict[str, Any]:
        return cast(dict[str, Any], _serialize_fact_value(value))


class SelectedProposalVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: UUID
    proposal_version: int = Field(ge=1)


class ExecutionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    proposals: tuple[SelectedProposalVersion, ...] = Field(min_length=1, max_length=500)


class ExecutionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    plan_id: UUID
    plan_version: int = Field(ge=1)
    input_target_version_id: UUID
    target_version: str = Field(min_length=1, max_length=128)
    counts: dict[OperationType, int]
    proposal_sources: dict[ProposalSource, int]
    operations: tuple[GovernanceOperation, ...]
    high_risk: bool


class ConfirmExecutionBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: UUID
    plan_version: int = Field(ge=1)
    high_risk_acknowledged: bool = False


class PreflightConflictCode(StrEnum):
    PROPOSAL_VERSION_DRIFT = "proposal_version_drift"
    DIFFERENCE_VERSION_DRIFT = "difference_version_drift"
    ANALYSIS_VERSION_DRIFT = "analysis_version_drift"
    TARGET_VERSION_DRIFT = "target_version_drift"
    BEFORE_VALUE_DRIFT = "before_value_drift"
    DEPENDENCY_MISSING = "dependency_missing"
    MAPPING_CONFLICT = "mapping_conflict"
    INELIGIBLE = "ineligible"


class PreflightConflict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: UUID | None = None
    code: PreflightConflictCode
    message: str = Field(min_length=1, max_length=1000)


class PreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    plan_id: UUID
    plan_version: int = Field(ge=1)
    target_version_id: UUID
    target_version: str = Field(min_length=1, max_length=128)
    conflicts: tuple[PreflightConflict, ...] = ()
    valid: bool


class ExecutionBatchConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: UUID
    plan_id: UUID
    plan_version: int = Field(ge=1)
    input_target_version_id: UUID
    status: ExecutionBatchStatus
    confirmed_by: str = Field(min_length=1, max_length=128)
    independent_reviewer_id: str | None = None
    high_risk_acknowledged: bool
    preflight: PreflightResult
    confirmed_at: datetime


class PlanExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=3, max_length=2000)
    risk_explanation: str = Field(min_length=3, max_length=2000)
    attention_points: tuple[str, ...] = Field(default=(), max_length=20)


class PlanExplanationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["available"] = "available"
    explanation: PlanExplanation
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=255)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    request_id: str | None = Field(default=None, max_length=255)


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    actual: dict[str, JsonValue] | None = None
    mismatches: dict[str, dict[str, JsonValue | None]] = Field(default_factory=dict)


class ExecutionOperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: UUID
    operation_id: UUID
    status: OperationStatus
    attempt_number: int = Field(ge=1)
    retryable: bool = False
    error_code: str | None = None
    actual_after: dict[str, JsonValue] | None = None
    verification: dict[str, JsonValue] | None = None


class ExecutionBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    status: ExecutionBatchStatus
    output_target_version_id: UUID | None = None
    operations: tuple[ExecutionOperationResult, ...]
    retryable_operation_ids: tuple[UUID, ...] = ()


class RetryExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_ids: tuple[UUID, ...] = Field(min_length=1, max_length=500)


class ExecutionAttemptView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_number: int = Field(ge=1)
    status: OperationStatus
    error_code: str | None = None
    error_detail: dict[str, JsonValue] | None = None
    actual_after: dict[str, JsonValue] | None = None
    verification: dict[str, JsonValue] | None = None
    retryable: bool
    target_version_id: UUID | None = None
    created_at: datetime


class ExecutionOperationView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: UUID
    operation_id: UUID
    proposal_id: UUID
    proposal_version: int = Field(ge=1)
    proposal_source: ProposalSource
    proposal_created_by: str
    difference_id: UUID
    difference_version: int = Field(ge=1)
    operation_type: OperationType
    entity_type: EntityType
    target_source_identifier: str | None = None
    before: dict[str, JsonValue] | None = None
    after: dict[str, JsonValue] | None = None
    risk: RiskLevel
    attempts: tuple[ExecutionAttemptView, ...]


class ExecutionAuditEventView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    operation_id: UUID | None = None
    actor_id: str
    event_type: str
    details: dict[str, JsonValue]
    created_at: datetime


class ExecutionRecordSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    task_id: UUID
    plan_id: UUID
    plan_version: int = Field(ge=1)
    status: ExecutionBatchStatus
    confirmed_by: str
    confirmed_at: datetime
    operation_count: int = Field(ge=0)
    retryable_count: int = Field(ge=0)
    output_target_version_id: UUID | None = None


class ExecutionRecordPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ExecutionRecordSummary, ...]
    next_cursor: str | None = None


class ExecutionRecordDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    task_id: UUID
    source_snapshot_id: UUID
    target_snapshot_id: UUID
    plan_id: UUID
    plan_version: int = Field(ge=1)
    plan_created_by: str
    status: ExecutionBatchStatus
    confirmed_by: str
    independent_reviewer_id: str | None = None
    high_risk_acknowledged: bool
    input_target_version_id: UUID
    output_target_version_ids: tuple[UUID, ...]
    confirmed_at: datetime
    operations: tuple[ExecutionOperationView, ...]
    audit_events: tuple[ExecutionAuditEventView, ...]
    permitted_actions: tuple[str, ...]

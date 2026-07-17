import json
from collections.abc import Mapping
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any, cast
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
        return MappingProxyType(
            {key: _freeze_fact_value(item) for key, item in value.items()}
        )
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
    def freeze_facts(
        cls, value: Mapping[str, JsonValue] | None
    ) -> Mapping[str, JsonValue] | None:
        if value is None:
            return None
        return cast(Mapping[str, JsonValue], _freeze_fact_value(value))

    @field_serializer("before", "after")
    def serialize_facts(
        self, value: Mapping[str, JsonValue] | None
    ) -> dict[str, Any] | None:
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
    def freeze_facts(
        cls, value: Mapping[str, JsonValue] | None
    ) -> Mapping[str, JsonValue] | None:
        if value is None:
            return None
        return cast(Mapping[str, JsonValue], _freeze_fact_value(value))

    @field_serializer("before", "after")
    def serialize_facts(
        self, value: Mapping[str, JsonValue] | None
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return cast(dict[str, Any], _serialize_fact_value(value))

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "GovernanceOperation":
        has_target = (
            self.target_entity_id is not None or self.target_source_identifier is not None
        )

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

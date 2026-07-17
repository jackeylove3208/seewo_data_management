from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.schemas.canonical_entities import EntityType
from app.schemas.governance import RiskLevel


def _freeze_fact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_fact_value(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_fact_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_fact_value(item) for item in value)
    return value


def _serialize_fact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _serialize_fact_value(item) for key, item in value.items()}
    if isinstance(value, tuple | frozenset):
        return [_serialize_fact_value(item) for item in value]
    return value


class OperationType(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    MOVE = "move"
    DISABLE = "disable"
    SKIP = "skip"


class ProposalSource(StrEnum):
    AI = "ai"
    OPERATOR = "operator"


class ProposalVersionRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    proposal_id: UUID
    proposal_version: int = Field(ge=1)


class GovernanceOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: UUID = Field(default_factory=uuid4)
    proposal: ProposalVersionRef
    proposal_source: ProposalSource
    difference_id: UUID
    difference_version: int = Field(ge=1)
    operation_type: OperationType
    entity_type: EntityType
    target_entity_id: UUID | None = None
    target_source_identifier: str | None = Field(default=None, min_length=1, max_length=255)
    before: Mapping[str, Any] | None = None
    after: Mapping[str, Any] | None = None
    changed_fields: frozenset[str] = Field(default_factory=frozenset)
    dependencies: frozenset[UUID] = Field(default_factory=frozenset)
    reversible: bool
    risk: RiskLevel
    compensation_for: UUID | None = None
    restore_absence: bool = False

    @field_validator("before", "after", mode="after")
    @classmethod
    def freeze_facts(cls, value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        if value is None:
            return None
        return _freeze_fact_value(value)

    @field_serializer("before", "after", when_used="json")
    def serialize_facts(self, value: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return _serialize_fact_value(value)

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
            self.after is not None and self.after != self.before
        ):
            raise ValueError("skip operations must be non-mutating")

        return self

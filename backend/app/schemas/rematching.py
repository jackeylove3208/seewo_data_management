import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.canonical_entities import EntityType

_CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class KeyGroupPolicy(_Contract):
    """One AND group inside a versioned OR-of-AND matching policy."""

    name: str = Field(min_length=1, max_length=128)
    fields: tuple[str, ...] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, fields: tuple[str, ...]) -> tuple[str, ...]:
        if any(not field.strip() for field in fields):
            raise ValueError("key-group fields cannot be blank")
        if len(set(fields)) != len(fields):
            raise ValueError("key-group fields must be unique")
        return fields

    def is_complete(self, values: dict[str, str | None]) -> bool:
        for field in self.fields:
            value = values.get(field)
            if value is None or not value.strip():
                return False
        return True


class VersionedKeyPolicy(_Contract):
    version: str = Field(min_length=1, max_length=64)
    entity_type: EntityType
    groups: tuple[KeyGroupPolicy, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_group_names(self) -> "VersionedKeyPolicy":
        names = [group.name for group in self.groups]
        if len(set(names)) != len(names):
            raise ValueError("key-policy group keys must be unique")
        return self

    def complete_groups(self, values: dict[str, str | None]) -> tuple[KeyGroupPolicy, ...]:
        return tuple(group for group in self.groups if group.is_complete(values))


class TrustedSourceIdentifierPolicy(_Contract):
    version: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=128)
    entity_type: EntityType
    source_snapshot_id: UUID
    target_snapshot_id: UUID
    trusted: bool = False
    field: str = Field(default="source_id", min_length=1, max_length=128)

    @property
    def can_auto_match(self) -> bool:
        return self.trusted

class MatchingQualityCounts(_Contract):
    total: int = Field(ge=0)
    accepted: int = Field(ge=0)
    deterministic: int = Field(ge=0)
    ai_recovered: int = Field(ge=0)
    manual_review: int = Field(ge=0)
    conflict: int = Field(ge=0)
    unmatched: int = Field(ge=0)
    unconsumed_target: int = Field(ge=0)
    predicted_missing: int = Field(ge=0)
    predicted_redundant: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_partition(self) -> "MatchingQualityCounts":
        if self.accepted != self.deterministic + self.ai_recovered:
            raise ValueError("accepted count must equal deterministic and AI-recovered counts")
        if self.total != self.accepted + self.manual_review + self.conflict + self.unmatched:
            raise ValueError("total count must equal mapping outcome counts")
        if self.predicted_missing != self.unmatched:
            raise ValueError("predicted-missing count must equal unmatched count")
        if self.predicted_redundant != self.unconsumed_target:
            raise ValueError("predicted-redundant count must equal unconsumed-target count")
        return self

    @property
    def remaining_unresolved(self) -> int:
        return self.manual_review + self.conflict + self.unmatched

    @property
    def unresolved_ratio(self) -> float:
        return self.remaining_unresolved / self.total if self.total else 0.0


class MatchingQualityGate(_Contract):
    code: Literal["matching_quality_gate_failed"] = "matching_quality_gate_failed"
    affected_entity_types: tuple[EntityType, ...] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)
    observed_value: float = Field(ge=0)
    threshold: float = Field(ge=0)
    recovery_actions: tuple[str, ...] = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def require_chinese_reason(cls, reason: str) -> str:
        if _CHINESE_TEXT.search(reason) is None:
            raise ValueError("quality-gate reason must contain Chinese business text")
        return reason

    @field_validator("recovery_actions")
    @classmethod
    def require_chinese_actions(cls, actions: tuple[str, ...]) -> tuple[str, ...]:
        if any(_CHINESE_TEXT.search(action) is None for action in actions):
            raise ValueError("recovery actions must contain Chinese business text")
        return actions


class MatchingQualityResult(_Contract):
    task_id: UUID
    policy_version: str = Field(min_length=1, max_length=64)
    mapping_versions: tuple[str, ...] = Field(min_length=1)
    counts: dict[EntityType, MatchingQualityCounts]
    passed: bool
    failures: tuple[MatchingQualityGate, ...] = ()

    @model_validator(mode="after")
    def validate_gate_outcome(self) -> "MatchingQualityResult":
        if self.passed and self.failures:
            raise ValueError("passed result cannot contain gate failures")
        if not self.passed and not self.failures:
            raise ValueError("failed result requires at least one gate failure")
        return self

    @property
    def retryable(self) -> bool:
        return not self.passed

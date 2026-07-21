from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.canonical_entities import EntityType


class MatchMethod(StrEnum):
    HISTORICAL = "historical"
    STABLE_ID = "stable_id"
    COMPOSITE_KEY = "composite_key"
    SCORED = "scored"


class MatchStatus(StrEnum):
    ACCEPTED = "accepted"
    MANUAL_REVIEW = "manual_review"
    UNMATCHED = "unmatched"
    CONFLICT = "conflict"


class MatchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature: str = Field(min_length=1, max_length=128)
    source_value: str | None
    target_value: str | None
    score: float = Field(ge=0, le=1)


class MatchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: EntityType
    source_entity_id: UUID
    source_key: str = Field(min_length=1, max_length=512)
    target_entity_id: UUID | None = None
    target_key: str | None = Field(default=None, max_length=512)
    method: MatchMethod | None = None
    status: MatchStatus
    confidence: float = Field(ge=0, le=1)
    evidence: tuple[MatchEvidence, ...] = ()
    rule_version: str = Field(min_length=1, max_length=64)
    confirmed_by: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_target_pair(self) -> "MatchDecision":
        has_entity = self.target_entity_id is not None
        has_key = self.target_key is not None
        if has_entity != has_key:
            raise ValueError("target_entity_id and target_key must be supplied together")
        if self.status is MatchStatus.ACCEPTED and not has_entity:
            raise ValueError("accepted decisions require a target")
        return self


class NormalizedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: UUID
    snapshot_id: UUID
    tenant_id: str = Field(min_length=1, max_length=128)
    entity_type: EntityType
    source_id: str = Field(min_length=1, max_length=255)
    values: dict[str, str | None]
    parent_mapping_id: UUID | None = None
    rule_version: str = Field(min_length=1, max_length=64)

    @property
    def record_key(self) -> str:
        return f"{self.entity_type.value}:{self.source_id}"


class BlockKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    entity_type: EntityType
    campus_id: str | None = Field(default=None, max_length=255)
    grade: str | None = Field(default=None, max_length=64)
    parent_mapping_id: UUID | None = None


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: NormalizedRecord
    block_key: BlockKey
    lexical_score: float | None = Field(default=None, ge=0, le=1)
    vector_score: float | None = Field(default=None, ge=0, le=1)
    retrieval_scope: Literal["strict", "relaxed"] = "strict"

    @property
    def entity_id(self) -> UUID:
        return self.entity.entity_id


class SnapshotPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    tenant_id: str = Field(min_length=1, max_length=128)
    source_snapshot_id: UUID
    target_snapshot_id: UUID


class ResolutionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    source_snapshot_id: UUID
    target_snapshot_id: UUID
    processed_entity_types: tuple[EntityType, ...]
    decisions: tuple[MatchDecision, ...]
    counts: dict[MatchStatus, int]

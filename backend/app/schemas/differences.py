import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.canonical_entities import EntityType, SourceRole
from app.schemas.matching import MatchEvidence


class DifferenceType(StrEnum):
    SEEWO_MISSING = "seewo_missing"
    SEEWO_REDUNDANT = "seewo_redundant"
    ATTRIBUTE_CONFLICT = "attribute_conflict"
    STRUCTURE_CONFLICT = "structure_conflict"
    DUPLICATE_CONFLICT = "duplicate_conflict"


class DifferenceStatus(StrEnum):
    OPEN = "open"
    SELECTED = "selected"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class DifferenceAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    MOVE = "move"
    DISABLE = "disable"
    MANUAL_REVIEW = "manual_review"


class FieldDifference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1, max_length=128)
    source_value: Any = None
    target_value: Any = None
    normalized_source: Any = None
    normalized_target: Any = None
    comparison: Literal["attribute", "structure", "duplicate"]


class DifferenceEntityReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: UUID
    entity_type: EntityType
    source_role: SourceRole
    source_id: str = Field(min_length=1, max_length=255)
    raw_row_number: int = Field(ge=1)
    payload: dict[str, Any]
    raw_payload: dict[str, Any] | None = None


class DifferenceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_snapshot_id: UUID
    target_snapshot_id: UUID
    source_entity_id: UUID | None = None
    target_entity_id: UUID | None = None
    mapping_id: UUID | None = None
    fields: tuple[FieldDifference, ...] = ()
    match_evidence: tuple[MatchEvidence, ...] = ()
    raw_source_row: int | None = Field(default=None, ge=1)
    raw_target_row: int | None = Field(default=None, ge=1)
    source_payload: dict[str, Any] | None = None
    target_payload: dict[str, Any] | None = None
    raw_source_payload: dict[str, Any] | None = None
    raw_target_payload: dict[str, Any] | None = None
    related_entities: tuple[DifferenceEntityReference, ...] = ()
    comparison_rule_version: str = Field(min_length=1, max_length=64)


class DifferenceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    tenant_id: str = Field(min_length=1, max_length=128)
    entity_type: EntityType
    difference_type: DifferenceType
    proposed_action: DifferenceAction
    evidence: DifferenceEvidence
    status: DifferenceStatus = DifferenceStatus.OPEN
    version: int = Field(default=1, ge=1)

    def evidence_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"status"})
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class DifferenceItem(DifferenceDraft):
    id: UUID
    created_at: datetime
    analysis_status: str = "pending"
    risk: str | None = None


class DifferenceFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: EntityType | None = None
    difference_type: DifferenceType | None = None
    analysis_status: str | None = Field(default=None, max_length=32)
    risk: str | None = Field(default=None, max_length=32)
    resolution_status: DifferenceStatus | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class DifferencePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[DifferenceItem, ...]
    next_cursor: str | None = None


class DifferenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    difference_ids: tuple[UUID, ...]
    counts: dict[DifferenceType, int]
    processed_entities: int = Field(ge=0)
    compared_pairs: int = Field(ge=0)

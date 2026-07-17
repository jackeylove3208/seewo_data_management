from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.canonical_entities import CanonicalEntity, EntityType, SourceRole


class SnapshotMode(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


class SnapshotScope(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    mode: SnapshotMode
    entity_types: frozenset[EntityType] = Field(default_factory=lambda: frozenset(EntityType))

    @property
    def allows_redundant_detection(self) -> bool:
        return self.mode is SnapshotMode.FULL


class CanonicalBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: UUID
    source_role: SourceRole
    entities: tuple[CanonicalEntity, ...]


class IngestionIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    row_number: int | None = None
    code: str
    message: str
    field: str | None = None
    original_value: str | None = None


class IngestionSummary(BaseModel):
    accepted: int = 0
    normalized_with_warning: int = 0
    quarantined: int = 0
    rejected: int = 0


class ConnectorReadResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch: CanonicalBatch
    raw_rows: tuple[dict[str, object], ...]
    summary: IngestionSummary
    warnings: tuple[IngestionIssue, ...] = ()
    quarantined: tuple[IngestionIssue, ...] = ()

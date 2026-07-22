from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.canonical_entities import EntityType, SourceRole
from app.schemas.ingestion import IngestionSummary, SnapshotMode
from app.schemas.workflow import WorkflowState


class UploadResponse(BaseModel):
    id: UUID
    source_role: SourceRole
    original_name: str
    sha256: str
    size_bytes: int
    detected_encoding: str


class FieldMappingSummary(BaseModel):
    version: str
    name: str
    source_role: SourceRole


class FieldMappingPreviewRequest(BaseModel):
    mapping_version: str


class FieldMappingPreviewResponse(BaseModel):
    mapping_version: str
    headers: tuple[str, ...]
    mapped_columns: dict[str, str]
    sample_rows: tuple[dict[str, Any], ...]


class CreateReconciliationTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authoritative_upload_id: UUID
    target_upload_id: UUID
    scope_id: str = Field(min_length=1)
    snapshot_mode: SnapshotMode
    entity_types: frozenset[EntityType] = Field(default_factory=lambda: frozenset(EntityType))
    schema_version: str = "canonical-v1"
    authoritative_mapping_version: str
    target_mapping_version: str


class SnapshotSummaryResponse(IngestionSummary):
    id: UUID
    schema_version: str
    mapping_version: str
    file_hash: str
    content_hash: str
    quarantine_available: bool


class ReconciliationTaskResponse(BaseModel):
    id: UUID
    tenant_id: str
    workflow_version: str
    scope_id: str
    snapshot_mode: SnapshotMode
    entity_types: tuple[EntityType, ...]
    status: str
    stage: str
    snapshots: dict[SourceRole, SnapshotSummaryResponse]
    workflow: WorkflowState
    error: dict[str, Any] | None = None

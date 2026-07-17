from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from app.connectors.base import (
    ConnectorMutationNotImplemented,
    ConnectorReadError,
    ConnectorReadRequest,
    ConnectorVersion,
)
from app.ingestion.csv_reader import inspect_csv, read_csv_frame
from app.ingestion.field_mapping import FieldMappingProfile
from app.ingestion.schema_validation import validate_frame
from app.schemas.canonical_entities import SourceRole
from app.schemas.ingestion import CanonicalBatch, ConnectorReadResult, IngestionSummary


class MofaCsvConnector:
    def __init__(
        self,
        *,
        path: Path,
        profile: FieldMappingProfile,
        tenant_id: str,
        snapshot_id: UUID,
    ) -> None:
        if profile.source_role is not SourceRole.TARGET:
            raise ValueError("Mofa CSV requires a target mapping profile")
        self.path = path
        self.profile = profile
        self.tenant_id = tenant_id
        self.snapshot_id = snapshot_id

    async def version(self) -> ConnectorVersion:
        digest = sha256()
        with self.path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return ConnectorVersion(value=f"sha256:{digest.hexdigest()}")

    async def read(self, request: ConnectorReadRequest) -> ConnectorReadResult:
        inspection = inspect_csv(self.path)
        frame = read_csv_frame(self.path, inspection)
        validation = validate_frame(
            frame,
            profile=self.profile,
            tenant_id=self.tenant_id,
            snapshot_id=self.snapshot_id,
            source_role=SourceRole.TARGET,
        )
        if validation.fatal_errors:
            raise ConnectorReadError(validation.fatal_errors)
        entities = validation.entities
        if request.entity_types is not None:
            entities = tuple(
                entity for entity in entities if entity.entity_type in request.entity_types
            )
        return ConnectorReadResult(
            batch=CanonicalBatch(
                snapshot_id=self.snapshot_id,
                source_role=SourceRole.TARGET,
                entities=entities,
            ),
            raw_rows=validation.raw_rows,
            summary=IngestionSummary(
                accepted=len(entities),
                normalized_with_warning=validation.summary.normalized_with_warning,
                quarantined=validation.summary.quarantined,
                rejected=validation.summary.rejected,
            ),
            warnings=validation.warnings,
            quarantined=validation.quarantined,
        )

    async def apply(
        self,
        operations: list[dict[str, Any]],
        idempotency_key: str,
    ) -> ConnectorVersion:
        raise ConnectorMutationNotImplemented(
            "CSV target mutation is implemented by the governance-execution module"
        )

    async def verify(self, expected: list[dict[str, Any]]) -> list[bool]:
        raise ConnectorMutationNotImplemented(
            "CSV target verification is implemented by the governance-execution module"
        )

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import ConnectorReadError, ConnectorReadRequest
from app.connectors.csv_source import ThirdPartyCsvConnector
from app.connectors.csv_target import MofaCsvConnector
from app.core.config import Settings
from app.ingestion.csv_reader import CsvFormatError, inspect_csv, read_csv_frame
from app.ingestion.field_mapping import FieldMappingProfile, FieldMappingRegistry
from app.ingestion.quarantine import write_quarantine
from app.ingestion.upload_storage import UploadStorage, UploadTooLarge
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.files import FileRepository
from app.repositories.snapshots import SnapshotDraft, SnapshotRepository
from app.repositories.tasks import TaskRepository
from app.repositories.workflow import WorkflowRunRepository
from app.schemas.api_ingestion import (
    CreateReconciliationTaskRequest,
    FieldMappingPreviewResponse,
    ReconciliationTaskResponse,
    SnapshotSummaryResponse,
    UploadResponse,
)
from app.schemas.canonical_entities import EntityType, SourceRole
from app.schemas.ingestion import IngestionIssue, SnapshotScope
from app.schemas.workflow import WorkflowState


class IngestionServiceError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details

    def as_detail(self) -> dict[str, object]:
        detail: dict[str, object] = {"code": self.code, "message": self.message}
        if self.details is not None:
            detail["details"] = self.details
        return detail


class UploadService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.files = FileRepository(session)

    async def store(self, upload: UploadFile, source_role: SourceRole) -> UploadResponse:
        storage = UploadStorage(self.settings.upload_root, self.settings.max_upload_bytes)
        try:
            stored = storage.save(upload.file, upload.filename or "upload.csv")
            inspection = inspect_csv(stored.path)
        except (CsvFormatError, UploadTooLarge, ValueError) as error:
            if "stored" in locals():
                stored.path.unlink(missing_ok=True)
            raise IngestionServiceError(422, "invalid_csv", str(error)) from error
        record = await self.files.create(
            source_role=source_role,
            original_name=stored.original_name,
            storage_name=stored.storage_name,
            storage_path=stored.path,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            detected_encoding=inspection.encoding,
        )
        await self.session.flush()
        return UploadResponse(
            id=record.id,
            source_role=source_role,
            original_name=record.original_name,
            sha256=record.sha256,
            size_bytes=record.size_bytes,
            detected_encoding=inspection.encoding,
        )

    async def preview(
        self,
        upload_id: UUID,
        mapping_version: str,
        mappings: FieldMappingRegistry,
        *,
        sample_limit: int = 5,
    ) -> FieldMappingPreviewResponse:
        source_file = await self.files.get(upload_id)
        if source_file is None:
            raise IngestionServiceError(404, "upload_not_found", "upload does not exist")
        try:
            profile = mappings.get(mapping_version)
        except LookupError as error:
            raise IngestionServiceError(422, "unknown_mapping", str(error)) from error
        if profile.source_role.value != source_file.source_role:
            raise IngestionServiceError(
                422,
                "mapping_role_mismatch",
                "mapping profile does not match upload source role",
            )
        path = Path(source_file.storage_path)
        inspection = inspect_csv(path)
        frame = read_csv_frame(path, inspection)
        sample = frame.drop("_row_number").head(sample_limit).to_dicts()
        return FieldMappingPreviewResponse(
            mapping_version=profile.version,
            headers=inspection.headers,
            mapped_columns={
                canonical: source
                for canonical, source in profile.columns.items()
                if source in inspection.headers
            },
            sample_rows=tuple(sample),
        )


class ReconciliationIngestionService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        mappings: FieldMappingRegistry,
    ) -> None:
        self.session = session
        self.settings = settings
        self.mappings = mappings
        self.tasks = TaskRepository(session)
        self.files = FileRepository(session)
        self.snapshots = SnapshotRepository(session)
        self.workflow_runs = WorkflowRunRepository(session)

    async def create_task(
        self,
        request: CreateReconciliationTaskRequest,
        idempotency_key: str,
        tenant_id: str,
    ) -> ReconciliationTaskResponse:
        request_hash = _request_hash(request, tenant_id)
        existing = await self.tasks.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IngestionServiceError(
                    409,
                    "idempotency_conflict",
                    "idempotency key was already used for a different request",
                )
            return await self.get_task(existing.id, tenant_id)
        source_file = await self._required_file(
            request.authoritative_upload_id,
            SourceRole.AUTHORITATIVE,
        )
        target_file = await self._required_file(
            request.target_upload_id,
            SourceRole.TARGET,
        )
        if source_file.id == target_file.id:
            raise IngestionServiceError(
                422,
                "paired_upload_required",
                "authoritative and target uploads must be different files",
            )
        source_profile = self._mapping(
            request.authoritative_mapping_version,
            SourceRole.AUTHORITATIVE,
        )
        target_profile = self._mapping(
            request.target_mapping_version,
            SourceRole.TARGET,
        )
        scope = SnapshotScope(
            tenant_id=tenant_id,
            scope_id=request.scope_id,
            mode=request.snapshot_mode,
            entity_types=request.entity_types,
        )
        task = await self.tasks.create(
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            # This endpoint remains the legacy entry until the Agent task API is introduced.
            # Keeping the boundary explicit prevents a rollout flag from sending legacy CSV
            # requests into a partially deployed runtime.
            workflow_version="legacy-v1",
        )
        await self.session.flush()
        await self.files.bind_to_task(source_file.id, task.id)
        await self.files.bind_to_task(target_file.id, task.id)
        source_snapshot_id, target_snapshot_id = uuid4(), uuid4()
        source_connector = ThirdPartyCsvConnector(
            path=Path(source_file.storage_path),
            profile=source_profile,
            tenant_id=tenant_id,
            snapshot_id=source_snapshot_id,
        )
        target_connector = MofaCsvConnector(
            path=Path(target_file.storage_path),
            profile=target_profile,
            tenant_id=tenant_id,
            snapshot_id=target_snapshot_id,
        )
        try:
            source_result = await source_connector.read(
                ConnectorReadRequest(entity_types=request.entity_types)
            )
            target_result = await target_connector.read(
                ConnectorReadRequest(entity_types=request.entity_types)
            )
        except ConnectorReadError as error:
            details = [
                issue.model_dump(mode="json")
                for issue in error.issues
                if hasattr(issue, "model_dump")
            ]
            raise IngestionServiceError(
                422,
                "ingestion_blocked",
                "CSV validation failed before snapshot publication",
                details=details,
            ) from error
        source_quarantine = self._write_quarantine(
            source_snapshot_id,
            source_result.quarantined,
        )
        target_quarantine = self._write_quarantine(
            target_snapshot_id,
            target_result.quarantined,
        )
        await self.snapshots.publish_pair(
            task.id,
            SnapshotDraft(
                id=source_snapshot_id,
                source_file_id=source_file.id,
                source_role=SourceRole.AUTHORITATIVE,
                file_hash=source_file.sha256,
                schema_version=request.schema_version,
                mapping_version=source_profile.version,
                raw_rows=source_result.raw_rows,
                entities=source_result.batch.entities,
                summary=source_result.summary,
                warnings=source_result.warnings,
                quarantined=source_result.quarantined,
                quarantine_path=str(source_quarantine) if source_quarantine else None,
            ),
            SnapshotDraft(
                id=target_snapshot_id,
                source_file_id=target_file.id,
                source_role=SourceRole.TARGET,
                file_hash=target_file.sha256,
                schema_version=request.schema_version,
                mapping_version=target_profile.version,
                raw_rows=target_result.raw_rows,
                entities=target_result.batch.entities,
                summary=target_result.summary,
                warnings=target_result.warnings,
                quarantined=target_result.quarantined,
                quarantine_path=str(target_quarantine) if target_quarantine else None,
            ),
        )
        await self.tasks.mark_ready(task)
        await self.session.flush()
        return await self.get_task(task.id, tenant_id)

    async def get_task(self, task_id: UUID, tenant_id: str) -> ReconciliationTaskResponse:
        task = await self.tasks.get(task_id)
        if task is None or task.tenant_id != tenant_id:
            raise IngestionServiceError(404, "task_not_found", "task does not exist")
        snapshots = await self.snapshots.list_published(task.id)
        workflow = await self.workflow_runs.state(task)
        return _task_response(task, snapshots, workflow)

    async def quarantine_path(
        self,
        task_id: UUID,
        source_role: SourceRole,
        tenant_id: str,
    ) -> Path:
        task = await self.tasks.get(task_id)
        if task is None or task.tenant_id != tenant_id:
            raise IngestionServiceError(404, "task_not_found", "task does not exist")
        snapshot = await self.snapshots.get_for_task_role(task_id, source_role)
        if snapshot is None or snapshot.quarantine_path is None:
            raise IngestionServiceError(
                404,
                "quarantine_not_found",
                "this snapshot has no quarantine artifact",
            )
        return Path(snapshot.quarantine_path)

    async def _required_file(self, file_id: UUID, role: SourceRole) -> SourceFile:
        source_file = await self.files.get(file_id)
        if source_file is None:
            raise IngestionServiceError(404, "upload_not_found", f"{role.value} upload not found")
        if source_file.source_role != role.value:
            raise IngestionServiceError(
                422,
                "upload_role_mismatch",
                f"upload {file_id} is not {role.value}",
            )
        if source_file.task_id is not None:
            raise IngestionServiceError(
                409,
                "upload_already_used",
                f"upload {file_id} is already bound to a task",
            )
        return source_file

    def _mapping(self, version: str, role: SourceRole) -> FieldMappingProfile:
        try:
            profile = self.mappings.get(version)
        except LookupError as error:
            raise IngestionServiceError(422, "unknown_mapping", str(error)) from error
        if profile.source_role is not role:
            raise IngestionServiceError(
                422,
                "mapping_role_mismatch",
                f"mapping {version} does not belong to {role.value}",
            )
        return profile

    def _write_quarantine(
        self,
        snapshot_id: UUID,
        issues: tuple[IngestionIssue, ...],
    ) -> Path | None:
        if not issues:
            return None
        return write_quarantine(
            self.settings.quarantine_root / f"{snapshot_id}.csv",
            issues,
        )


def _request_hash(request: CreateReconciliationTaskRequest, tenant_id: str) -> str:
    payload = json.dumps(
        {"tenant_id": tenant_id, **request.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _task_response(
    task: ReconciliationTask,
    snapshots: tuple[Snapshot, ...],
    workflow: WorkflowState,
) -> ReconciliationTaskResponse:
    snapshot_responses = {
        SourceRole(snapshot.source_role): SnapshotSummaryResponse(
            id=snapshot.id,
            schema_version=snapshot.schema_version,
            mapping_version=snapshot.mapping_version,
            file_hash=snapshot.file_hash,
            content_hash=snapshot.content_hash,
            quarantine_available=snapshot.quarantine_path is not None,
            **snapshot.summary,
        )
        for snapshot in snapshots
    }
    return ReconciliationTaskResponse(
        id=task.id,
        tenant_id=task.tenant_id,
        workflow_version=task.workflow_version,
        scope_id=task.scope_id,
        snapshot_mode=task.snapshot_mode,
        entity_types=tuple(EntityType(value) for value in task.entity_types),
        status=task.status,
        stage=task.stage,
        snapshots=snapshot_responses,
        workflow=workflow,
        error=task.error,
    )

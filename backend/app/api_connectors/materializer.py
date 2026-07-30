import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import anyio
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_connectors.contracts import (
    ApiProviderError,
    CapturedApiPage,
    FrozenApiRecord,
    OrganizationApiAdapter,
)
from app.api_connectors.registry import ProviderRegistry
from app.api_connectors.secrets import (
    EncryptedDatabaseSecretStore,
    SecretReferenceError,
)
from app.core.config import Settings
from app.models.api_connectors import (
    AgentSourceBindingRecord,
    ApiAuthoritySourceRecord,
    ApiConnectionRecord,
)
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.schemas.agent_ingestion import AgentEntityKind

_ARTIFACT_CONTRACT_VERSION = "api-authority-jsonl-v1"


class ApiSourceFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ApiAuthorityMaterializer:
    """Capture one complete provider version before publishing Graph evidence."""

    def __init__(
        self,
        settings: Settings,
        *,
        registry: ProviderRegistry,
        fernet_key: bytes | str | SecretStr,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._fernet_key = fernet_key

    async def materialize(
        self,
        session: AsyncSession,
        *,
        task_id: UUID,
        api_source_id: UUID,
    ) -> SourceFile:
        task = await session.get(ReconciliationTask, task_id)
        if task is None:
            raise LookupError("API authority task not found")
        record = await session.scalar(
            select(ApiAuthoritySourceRecord)
            .where(
                ApiAuthoritySourceRecord.id == api_source_id,
                ApiAuthoritySourceRecord.task_id == task_id,
                ApiAuthoritySourceRecord.tenant_id == task.tenant_id,
            )
            .with_for_update()
        )
        if record is None:
            raise LookupError("Task-bound API authority source not found")
        if record.state == "ready":
            return await self._ready_source(session, record, task_id=task_id)
        if record.state not in {"registered", "materializing", "failed"}:
            raise ApiSourceFailure("connector_source_state_invalid")

        record.state = "materializing"
        record.safe_problem_code = None
        authority_root = self._settings.upload_root / "api-authority"
        await anyio.Path(authority_root).mkdir(parents=True, exist_ok=True)
        temporary = authority_root / f".{record.id.hex}-{uuid4().hex}.part"
        try:
            connection = await self._connection(session, record)
            adapter = self._adapter(record, connection)
            selected_entities = _selected_entities(record)
            secret = await EncryptedDatabaseSecretStore(
                session,
                fernet_key=self._fernet_key,
            ).get(
                tenant_id=record.tenant_id,
                secret_ref=record.frozen_secret_ref,
            )
            pages = [
                page
                async for page in adapter.capture(
                    record.frozen_public_configuration,
                    secret,
                    selected_entities,
                )
            ]
            frozen_records = _validate_capture(
                pages,
                selected_entities=selected_entities,
                maximum_pages=adapter.manifest.maximum_pages,
            )
            source_id = uuid5(NAMESPACE_URL, f"api-authority-source-file:{record.id}")
            snapshot_id = uuid5(NAMESPACE_URL, f"api-authority-snapshot:{record.id}")
            content = _artifact_content(
                task=task,
                source=record,
                connection=connection,
                source_file_id=source_id,
                snapshot_id=snapshot_id,
                records=frozen_records,
                page_count=len(pages),
            )
            if len(content) > self._settings.max_upload_bytes:
                raise ApiSourceFailure("connector_artifact_too_large")
            content_sha256 = hashlib.sha256(content).hexdigest()
            storage_name = f"api-{record.id.hex}-{content_sha256}.jsonl"
            final_path = authority_root / storage_name
            await anyio.to_thread.run_sync(_write_bytes, temporary, content)
            await _publish_file(temporary, final_path, expected_sha256=content_sha256)
            source_file = SourceFile(
                id=source_id,
                task_id=task.id,
                source_role="authoritative",
                original_name=f"{connection.provider_id}-authority-{record.id}.jsonl",
                storage_name=storage_name,
                storage_path=str(final_path),
                managed_storage=True,
                sha256=content_sha256,
                size_bytes=len(content),
                detected_encoding="utf-8",
            )
            snapshot = Snapshot(
                id=snapshot_id,
                task_id=task.id,
                source_file_id=source_file.id,
                source_role="authoritative",
                schema_version=_ARTIFACT_CONTRACT_VERSION,
                mapping_version=record.projection_version,
                file_hash=content_sha256,
                content_hash=_snapshot_content_hash(
                    source_file_id=source_file.id,
                    content_sha256=content_sha256,
                    selection_hash=record.selection_hash,
                ),
                state="published",
                summary={
                    "total": len(frozen_records),
                    "accepted": len(frozen_records),
                    "warnings": sum(
                        1 for item in frozen_records if item.unavailable_fields
                    ),
                    "quarantined": 0,
                    "page_count": len(pages),
                },
            )
            session.add(source_file)
            await session.flush()
            session.add(snapshot)
            await session.flush()
            record.state = "ready"
            record.source_file_id = source_file.id
            record.snapshot_id = snapshot.id
            record.content_sha256 = content_sha256
            record.record_count = len(frozen_records)
            record.page_count = len(pages)
            record.captured_at = datetime.now(UTC)
            record.safe_problem_code = None
            binding = await session.scalar(
                select(AgentSourceBindingRecord).where(
                    AgentSourceBindingRecord.task_id == task.id,
                    AgentSourceBindingRecord.tenant_id == task.tenant_id,
                    AgentSourceBindingRecord.role == "authoritative",
                )
            )
            if binding is None or binding.configuration_id != str(connection.id):
                raise ApiSourceFailure("connector_source_binding_invalid")
            binding.snapshot_id = snapshot.id
            await session.flush()
            return source_file
        except ApiSourceFailure as error:
            await self._mark_failed(session, record, error.code, temporary)
            raise
        except ApiProviderError as error:
            await self._mark_failed(session, record, error.safe_code, temporary)
            raise ApiSourceFailure(error.safe_code) from error
        except SecretReferenceError as error:
            code = "connector_secret_unavailable"
            await self._mark_failed(session, record, code, temporary)
            raise ApiSourceFailure(code) from error
        except (OSError, TypeError, UnicodeError) as error:
            code = "connector_artifact_failed"
            await self._mark_failed(session, record, code, temporary)
            raise ApiSourceFailure(code) from error

    async def _connection(
        self,
        session: AsyncSession,
        source: ApiAuthoritySourceRecord,
    ) -> ApiConnectionRecord:
        connection = await session.scalar(
            select(ApiConnectionRecord).where(
                ApiConnectionRecord.id == source.connection_id,
                ApiConnectionRecord.tenant_id == source.tenant_id,
            )
        )
        if connection is None:
            raise ApiSourceFailure("connector_connection_unavailable")
        return connection

    def _adapter(
        self,
        source: ApiAuthoritySourceRecord,
        connection: ApiConnectionRecord,
    ) -> OrganizationApiAdapter:
        try:
            manifest, adapter = self._registry.resolve(
                connection.provider_id,
                manifest_version=source.manifest_version,
                adapter_version=source.adapter_version,
            )
        except KeyError as error:
            raise ApiSourceFailure("connector_provider_contract_unavailable") from error
        if source.projection_version != manifest.projection_version:
            raise ApiSourceFailure("connector_provider_contract_unavailable")
        return adapter

    @staticmethod
    async def _ready_source(
        session: AsyncSession,
        record: ApiAuthoritySourceRecord,
        *,
        task_id: UUID,
    ) -> SourceFile:
        if record.source_file_id is None or record.snapshot_id is None:
            raise ApiSourceFailure("connector_source_state_invalid")
        source = await session.get(SourceFile, record.source_file_id)
        snapshot = await session.get(Snapshot, record.snapshot_id)
        if (
            source is None
            or snapshot is None
            or source.task_id != task_id
            or source.source_role != "authoritative"
            or snapshot.task_id != task_id
            or snapshot.source_file_id != source.id
            or source.sha256 != record.content_sha256
        ):
            raise ApiSourceFailure("connector_source_state_invalid")
        return source

    @staticmethod
    async def _mark_failed(
        session: AsyncSession,
        record: ApiAuthoritySourceRecord,
        code: str,
        temporary: Path,
    ) -> None:
        record.state = "failed"
        record.safe_problem_code = code
        record.source_file_id = None
        record.snapshot_id = None
        record.content_sha256 = None
        record.record_count = None
        record.page_count = None
        record.captured_at = None
        await session.flush()
        await anyio.Path(temporary).unlink(missing_ok=True)


def _selected_entities(
    source: ApiAuthoritySourceRecord,
) -> frozenset[AgentEntityKind]:
    if (
        not isinstance(source.selected_entities, list)
        or not source.selected_entities
        or source.selection_hash
        != _selection_hash(tuple(sorted(str(item) for item in source.selected_entities)))
    ):
        raise ApiSourceFailure("connector_selection_invalid")
    try:
        return frozenset(AgentEntityKind(item) for item in source.selected_entities)
    except ValueError as error:
        raise ApiSourceFailure("connector_selection_invalid") from error


def _validate_capture(
    pages: Sequence[CapturedApiPage],
    *,
    selected_entities: frozenset[AgentEntityKind],
    maximum_pages: int,
) -> tuple[FrozenApiRecord, ...]:
    if not pages or len(pages) > maximum_pages:
        raise ApiSourceFailure("connector_pagination_incomplete")
    records: dict[tuple[str, str], FrozenApiRecord] = {}
    cursors: set[str] = set()
    for expected_page_number, page in enumerate(pages, start=1):
        if page.page_number != expected_page_number:
            raise ApiSourceFailure("connector_pagination_incomplete")
        is_last = expected_page_number == len(pages)
        if is_last and page.next_cursor is not None:
            raise ApiSourceFailure("connector_pagination_incomplete")
        if not is_last:
            if page.next_cursor is None or page.next_cursor in cursors:
                raise ApiSourceFailure("connector_pagination_incomplete")
            cursors.add(page.next_cursor)
        for record in page.records:
            if record.entity_kind not in selected_entities:
                raise ApiSourceFailure("connector_entity_unsupported")
            _reject_sensitive_payload(record.provider_fields)
            key = (record.entity_kind.value, record.external_id)
            if key in records:
                raise ApiSourceFailure("connector_duplicate_external_id")
            records[key] = record
    return tuple(records[key] for key in sorted(records))


def _reject_sensitive_payload(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(
                marker in normalized
                for marker in ("access_token", "app_secret", "corp_secret", "password")
            ):
                raise ApiSourceFailure("connector_sensitive_payload")
            _reject_sensitive_payload(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_sensitive_payload(item)


def _artifact_content(
    *,
    task: ReconciliationTask,
    source: ApiAuthoritySourceRecord,
    connection: ApiConnectionRecord,
    source_file_id: UUID,
    snapshot_id: UUID,
    records: tuple[FrozenApiRecord, ...],
    page_count: int,
) -> bytes:
    header = {
        "record_type": "header",
        "contract_version": _ARTIFACT_CONTRACT_VERSION,
        "task_id": str(task.id),
        "tenant_id": task.tenant_id,
        "api_source_id": str(source.id),
        "connection_id": str(connection.id),
        "provider_id": connection.provider_id,
        "source_file_id": str(source_file_id),
        "snapshot_id": str(snapshot_id),
        "selected_entities": sorted(source.selected_entities),
        "selection_hash": source.selection_hash,
        "manifest_version": source.manifest_version,
        "adapter_version": source.adapter_version,
        "projection_version": source.projection_version,
        "page_count": page_count,
        "record_count": len(records),
    }
    lines = [_json_line(header)]
    lines.extend(
        _json_line(
            {
                "record_type": "record",
                **record.model_dump(mode="json"),
            }
        )
        for record in records
    )
    return b"".join(lines)


def _json_line(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _write_bytes(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()


async def _publish_file(
    temporary: Path,
    final_path: Path,
    *,
    expected_sha256: str,
) -> None:
    if await anyio.Path(final_path).exists():
        actual = await anyio.to_thread.run_sync(_file_sha256, final_path)
        if actual != expected_sha256:
            raise ApiSourceFailure("connector_artifact_conflict")
        await anyio.Path(temporary).unlink(missing_ok=True)
        return
    await anyio.Path(temporary).replace(final_path)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _selection_hash(values: tuple[str, ...]) -> str:
    return hashlib.sha256(
        json.dumps(list(values), separators=(",", ":")).encode()
    ).hexdigest()


def _snapshot_content_hash(
    *,
    source_file_id: UUID,
    content_sha256: str,
    selection_hash: str,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "content_sha256": content_sha256,
                "selection_hash": selection_hash,
                "source_file_id": str(source_file_id),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()

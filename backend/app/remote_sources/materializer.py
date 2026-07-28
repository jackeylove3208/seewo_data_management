import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.ingestion.csv_reader import CsvFormatError, inspect_csv, read_csv_frame
from app.models.reconciliation import ReconciliationTask
from app.models.remote_sources import RemoteSourceRecord
from app.models.snapshots import Snapshot, SourceFile
from app.remote_sources.network import (
    DownloadedRemoteCsv,
    RemoteCsvDownloader,
    RemoteSourceFailure,
)
from app.remote_sources.repository import RemoteSourceRepository
from app.repositories.files import FileRepository
from app.schemas.canonical_entities import SourceRole


class RemoteDownloader(Protocol):
    async def download(self, url: str, destination: Path) -> DownloadedRemoteCsv: ...


class RemoteSourceMaterializer:
    def __init__(
        self,
        settings: Settings,
        *,
        downloader: RemoteDownloader | None = None,
    ) -> None:
        self.settings = settings
        self.downloader = downloader or RemoteCsvDownloader(
            max_redirects=settings.remote_source_max_redirects,
            max_bytes=settings.max_upload_bytes,
            connect_timeout=settings.remote_source_connect_timeout_seconds,
            read_timeout=settings.remote_source_read_timeout_seconds,
            total_timeout=settings.remote_source_total_timeout_seconds,
        )

    async def materialize(
        self,
        session: AsyncSession,
        *,
        task_id: UUID,
        remote_source_id: UUID,
    ) -> SourceFile:
        task = await session.get(ReconciliationTask, task_id)
        if task is None:
            raise LookupError("Remote source task not found")
        record = await session.scalar(
            select(RemoteSourceRecord)
            .where(
                RemoteSourceRecord.id == remote_source_id,
                RemoteSourceRecord.task_id == task_id,
                RemoteSourceRecord.tenant_id == task.tenant_id,
            )
            .with_for_update()
        )
        if record is None:
            raise LookupError("Task-bound remote source not found")
        if record.state == "ready":
            return await self._ready_source(session, record, task_id=task_id)
        if record.state not in {"registered", "materializing", "failed"}:
            raise RemoteSourceFailure(
                "remote_source_invalid_state",
                "网页数据来源当前不能进行物化。",
            )

        repository = RemoteSourceRepository(session)
        repository.mark_materializing(record)
        remote_root = self.settings.upload_root / "remote"
        await anyio.Path(remote_root).mkdir(parents=True, exist_ok=True)
        temporary = remote_root / f".{record.id.hex}-{uuid4().hex}.part"
        try:
            downloaded = await anyio.to_thread.run_sync(
                _recover_completed_download,
                remote_root,
                record.id,
            )
            if downloaded is None:
                downloaded = await self.downloader.download(
                    record.original_url,
                    temporary,
                )
                downloaded = await _publish_download(downloaded, remote_root, record.id)
            source = await self._publish_database_rows(
                session,
                task=task,
                record=record,
                downloaded=downloaded,
            )
            return source
        except RemoteSourceFailure as error:
            repository.mark_failed(record, safe_problem_code=error.code)
            await session.flush()
            await anyio.Path(temporary).unlink(missing_ok=True)
            raise

    @staticmethod
    async def _ready_source(
        session: AsyncSession,
        record: RemoteSourceRecord,
        *,
        task_id: UUID,
    ) -> SourceFile:
        if record.source_file_id is None:
            raise RemoteSourceFailure(
                "remote_source_invalid_state",
                "网页数据来源缺少已发布文件。",
            )
        source = await session.get(SourceFile, record.source_file_id)
        if (
            source is None
            or source.task_id != task_id
            or source.source_role != SourceRole.AUTHORITATIVE.value
        ):
            raise RemoteSourceFailure(
                "remote_source_invalid_state",
                "网页数据来源的已发布文件不一致。",
            )
        return source

    @staticmethod
    async def _publish_database_rows(
        session: AsyncSession,
        *,
        task: ReconciliationTask,
        record: RemoteSourceRecord,
        downloaded: DownloadedRemoteCsv,
    ) -> SourceFile:
        files = FileRepository(session)
        source = await files.create(
            source_role=SourceRole.AUTHORITATIVE,
            original_name=f"remote-source-{record.id}.csv",
            storage_name=downloaded.path.name,
            storage_path=downloaded.path,
            sha256=downloaded.content_sha256,
            size_bytes=downloaded.size_bytes,
            detected_encoding=downloaded.detected_encoding,
            managed_storage=True,
        )
        await session.flush()
        await files.bind_to_task(source.id, task.id)
        session.add(
            Snapshot(
                id=uuid4(),
                task_id=task.id,
                source_file_id=source.id,
                source_role=SourceRole.AUTHORITATIVE.value,
                schema_version="agent-contract-v1",
                mapping_version="agent-csv-v2",
                file_hash=downloaded.content_sha256,
                content_hash=_snapshot_content_hash(source),
                state="published",
                summary={"total": 0, "accepted": 0, "warnings": 0, "quarantined": 0},
            )
        )
        RemoteSourceRepository.mark_ready(
            record,
            source_file_id=source.id,
            content_sha256=downloaded.content_sha256,
            size_bytes=downloaded.size_bytes,
            media_type=downloaded.media_type,
            retrieved_at=datetime.now(UTC),
        )
        await session.flush()
        return source


async def _publish_download(
    downloaded: DownloadedRemoteCsv,
    remote_root: Path,
    remote_source_id: UUID,
) -> DownloadedRemoteCsv:
    final_path = (
        remote_root
        / f"{remote_source_id.hex}-{downloaded.content_sha256}.csv"
    )
    if await anyio.Path(final_path).exists():
        await anyio.Path(downloaded.path).unlink(missing_ok=True)
    else:
        await anyio.Path(downloaded.path).replace(final_path)
    return DownloadedRemoteCsv(
        path=final_path,
        content_sha256=downloaded.content_sha256,
        size_bytes=downloaded.size_bytes,
        media_type=downloaded.media_type,
        detected_encoding=downloaded.detected_encoding,
    )


def _recover_completed_download(
    remote_root: Path,
    remote_source_id: UUID,
) -> DownloadedRemoteCsv | None:
    candidates = tuple(remote_root.glob(f"{remote_source_id.hex}-*.csv"))
    if not candidates:
        return None
    valid: list[DownloadedRemoteCsv] = []
    for candidate in candidates:
        try:
            inspection = inspect_csv(candidate)
            read_csv_frame(candidate, inspection)
        except (CsvFormatError, OSError):
            continue
        digest = hashlib.sha256()
        size_bytes = 0
        with candidate.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size_bytes += len(chunk)
        content_sha256 = digest.hexdigest()
        expected_suffix = f"-{content_sha256}.csv"
        if not candidate.name.endswith(expected_suffix):
            continue
        valid.append(
            DownloadedRemoteCsv(
                path=candidate,
                content_sha256=content_sha256,
                size_bytes=size_bytes,
                media_type="text/csv",
                detected_encoding=inspection.encoding,
            )
        )
    if len(valid) > 1:
        raise RemoteSourceFailure(
            "remote_source_recovery_conflict",
            "网页数据存在多个已完成版本，无法安全恢复。",
        )
    return valid[0] if valid else None


def _snapshot_content_hash(source: SourceFile) -> str:
    return hashlib.sha256(
        (
            '{"sha256":"'
            + source.sha256
            + '","source_file_id":"'
            + str(source.id)
            + '"}'
        ).encode()
    ).hexdigest()

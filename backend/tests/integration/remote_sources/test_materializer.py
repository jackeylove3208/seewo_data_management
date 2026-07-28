import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import anyio
import pytest
from sqlalchemy import func, select

from app.agent_runtime.repository import AgentRuntimeRepository
from app.models.reconciliation import ReconciliationTask
from app.models.remote_sources import RemoteSourceRecord
from app.models.snapshots import Snapshot, SourceFile
from app.remote_sources.materializer import RemoteSourceMaterializer
from app.remote_sources.network import DownloadedRemoteCsv, RemoteSourceFailure
from tests.settings import build_test_settings


class StubDownloader:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[tuple[str, Path]] = []

    async def download(self, url: str, destination: Path) -> DownloadedRemoteCsv:
        self.calls.append((url, destination))
        await anyio.Path(destination.parent).mkdir(parents=True, exist_ok=True)
        await anyio.Path(destination).write_bytes(self.body)
        return DownloadedRemoteCsv(
            path=destination,
            content_sha256=hashlib.sha256(self.body).hexdigest(),
            size_bytes=len(self.body),
            media_type="text/csv",
            detected_encoding="utf-8",
        )


class FailingDownloader:
    async def download(self, url: str, destination: Path) -> DownloadedRemoteCsv:
        del url, destination
        raise RemoteSourceFailure(
            "remote_source_timeout",
            "第三方数据请求超时，请稍后重试。",
        )


async def _seed_bound_remote_source(session) -> tuple[ReconciliationTask, RemoteSourceRecord]:
    conversation = await AgentRuntimeRepository(session).create_conversation(
        tenant_id="school-1",
        created_by="operator-1",
    )
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        workflow_version="agent-graph-v1",
        task_kind="sync",
        title="远程学生同步",
        agent_intent={
            "source": {"kind": "remote_csv"},
            "target": {"kind": "local", "source_ref": "seewo/roster.csv"},
        },
        idempotency_key=uuid4().hex,
        request_hash=uuid4().hex * 2,
    )
    session.add(task)
    await session.flush()
    remote = RemoteSourceRecord(
        tenant_id="school-1",
        created_by="operator-1",
        conversation_id=conversation.id,
        task_id=task.id,
        original_url="https://data.example.test/roster.csv?secret=value",
        display_origin="data.example.test",
        state="registered",
    )
    session.add(remote)
    await session.flush()
    return task, remote


@pytest.mark.asyncio
async def test_materializes_one_immutable_authoritative_snapshot_idempotently(
    session,
    tmp_path: Path,
) -> None:
    task, remote = await _seed_bound_remote_source(session)
    body = "编号,姓名\nS001,张三\n".encode()
    downloader = StubDownloader(body)
    settings = build_test_settings(
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
    )
    materializer = RemoteSourceMaterializer(settings, downloader=downloader)

    first = await materializer.materialize(
        session,
        task_id=task.id,
        remote_source_id=remote.id,
    )
    second = await materializer.materialize(
        session,
        task_id=task.id,
        remote_source_id=remote.id,
    )

    assert second.id == first.id
    assert len(downloader.calls) == 1
    assert first.source_role == "authoritative"
    assert first.task_id == task.id
    assert first.managed_storage is True
    assert await anyio.Path(first.storage_path).read_bytes() == body
    assert "secret=value" not in first.original_name
    assert "https://" not in first.original_name
    assert "secret=value" not in first.storage_path
    assert remote.state == "ready"
    assert remote.source_file_id == first.id
    assert remote.content_sha256 == hashlib.sha256(body).hexdigest()
    assert remote.size_bytes == len(body)
    assert remote.media_type == "text/csv"
    assert remote.retrieved_at is not None
    snapshot = await session.scalar(
        select(Snapshot).where(
            Snapshot.task_id == task.id,
            Snapshot.source_role == "authoritative",
        )
    )
    assert snapshot is not None
    assert snapshot.mapping_version == "agent-csv-v2"
    assert await session.scalar(select(func.count(SourceFile.id))) == 1
    assert await session.scalar(select(func.count(Snapshot.id))) == 1


@pytest.mark.asyncio
async def test_materializer_reuses_completed_file_after_interrupted_publication(
    session,
    tmp_path: Path,
) -> None:
    task, remote = await _seed_bound_remote_source(session)
    body = b"id,name\nS001,Student\n"
    digest = hashlib.sha256(body).hexdigest()
    remote_root = tmp_path / "uploads/remote"
    remote_root.mkdir(parents=True)
    completed = remote_root / f"{remote.id.hex}-{digest}.csv"
    completed.write_bytes(body)
    downloader = StubDownloader(b"must not be requested")
    materializer = RemoteSourceMaterializer(
        build_test_settings(upload_root=tmp_path / "uploads"),
        downloader=downloader,
    )

    source = await materializer.materialize(
        session,
        task_id=task.id,
        remote_source_id=remote.id,
    )

    assert downloader.calls == []
    assert Path(source.storage_path) == completed
    assert source.sha256 == digest
    assert remote.state == "ready"


@pytest.mark.asyncio
async def test_materializer_records_only_safe_failure_without_partial_snapshot(
    session,
    tmp_path: Path,
) -> None:
    task, remote = await _seed_bound_remote_source(session)
    materializer = RemoteSourceMaterializer(
        build_test_settings(upload_root=tmp_path / "uploads"),
        downloader=FailingDownloader(),
    )

    with pytest.raises(RemoteSourceFailure) as raised:
        await materializer.materialize(
            session,
            task_id=task.id,
            remote_source_id=remote.id,
        )

    assert raised.value.code == "remote_source_timeout"
    assert remote.state == "failed"
    assert remote.safe_problem_code == "remote_source_timeout"
    assert remote.original_url not in str(raised.value)
    assert await session.scalar(select(func.count(SourceFile.id))) == 0
    assert await session.scalar(select(func.count(Snapshot.id))) == 0
    assert list((tmp_path / "uploads").rglob("*.part")) == []
    assert remote.retrieved_at is None
    assert datetime.now(UTC).timestamp() > remote.updated_at.timestamp()

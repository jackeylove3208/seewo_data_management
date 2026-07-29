import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from uuid import uuid4

import anyio
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_connectors.contracts import (
    CapturedApiPage,
    ConnectionTestResult,
    FrozenApiRecord,
    ProviderManifest,
)
from app.api_connectors.materializer import ApiAuthorityMaterializer, ApiSourceFailure
from app.api_connectors.registry import ProviderRegistry
from app.api_connectors.secrets import EncryptedDatabaseSecretStore
from app.core.config import Settings
from app.models.api_connectors import ApiAuthoritySourceRecord, ApiConnectionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.schemas.agent_ingestion import AgentEntityKind

MANIFEST = ProviderManifest(
    provider_id="fake-org",
    manifest_version="1.0.0",
    adapter_version="1.0.0",
    supported_entities=frozenset(AgentEntityKind),
    required_secret_fields=("client_id", "client_secret"),
    required_capabilities=("organization.read",),
    endpoint_hosts=("api.example.test",),
    maximum_pages=100,
    projection_version="organization-six-fields-v1",
)


class FakeCaptureAdapter:
    manifest = MANIFEST

    def __init__(self, pages: tuple[CapturedApiPage, ...]) -> None:
        self.pages = pages
        self.capture_calls = 0

    async def test_connection(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
    ) -> ConnectionTestResult:
        del public_configuration, secret
        raise NotImplementedError

    async def capture(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        selected_entities: frozenset[AgentEntityKind],
    ) -> AsyncIterator[CapturedApiPage]:
        assert public_configuration == {"person_entity_kind": "teacher"}
        del selected_entities
        assert secret == {"client_id": "client", "client_secret": "secret"}
        self.capture_calls += 1
        for page in self.pages:
            yield page

def _teacher(external_id: str, name: str = "周明远") -> FrozenApiRecord:
    return FrozenApiRecord(
        external_id=external_id,
        entity_kind=AgentEntityKind.TEACHER,
        provider_fields={"userid": external_id, "name": name},
        projected_fields={
            "category": "教师",
            "name": name,
            "number": None,
            "class_name": None,
            "phone": None,
            "email": None,
        },
        unavailable_fields=("email", "number", "phone"),
    )


async def _seed_source(
    session: AsyncSession,
    *,
    fernet_key: bytes,
) -> tuple[ReconciliationTask, ApiAuthoritySourceRecord]:
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["teacher"],
        status="running",
        stage="ingestion",
        workflow_version="agent-graph-v1",
        agent_intent={
            "source": {"kind": "api"},
            "target": {"kind": "database", "configuration_id": "seewo-mysql"},
        },
        idempotency_key=str(uuid4()),
        request_hash="a" * 64,
    )
    session.add(task)
    await session.flush()
    secret_ref = await EncryptedDatabaseSecretStore(
        session,
        fernet_key=fernet_key,
    ).put(
        tenant_id=task.tenant_id,
        payload={"client_id": "client", "client_secret": "secret"},
    )
    connection = ApiConnectionRecord(
        tenant_id=task.tenant_id,
        provider_id=MANIFEST.provider_id,
        display_name="权威通讯录",
        public_configuration={"person_entity_kind": "teacher"},
        secret_ref=secret_ref,
        manifest_version=MANIFEST.manifest_version,
        adapter_version=MANIFEST.adapter_version,
        capabilities={"entity.teacher.read": True},
        visibility_summary={"visible": True, "teacher_count": 2},
        state="active",
        created_by="operator-1",
        updated_by="operator-1",
    )
    session.add(connection)
    await session.flush()
    source = ApiAuthoritySourceRecord(
        tenant_id=task.tenant_id,
        task_id=task.id,
        connection_id=connection.id,
        frozen_public_configuration=dict(connection.public_configuration),
        frozen_secret_ref=connection.secret_ref,
        selected_entities=["teacher"],
        selection_hash=_selection_hash(("teacher",)),
        state="registered",
        manifest_version=MANIFEST.manifest_version,
        adapter_version=MANIFEST.adapter_version,
        projection_version=MANIFEST.projection_version,
    )
    session.add(source)
    await session.flush()
    return task, source


def _selection_hash(values: tuple[str, ...]) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(list(values), separators=(",", ":")).encode()
    ).hexdigest()


def _registry(adapter: FakeCaptureAdapter) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(MANIFEST, adapter)
    return registry


async def test_api_materializer_publishes_complete_immutable_jsonl_and_replays(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    task, api_source = await _seed_source(session, fernet_key=key)
    await session.commit()
    adapter = FakeCaptureAdapter(
        (
            CapturedApiPage(
                page_number=1,
                records=(_teacher("teacher-2", "叶舒桐"),),
                next_cursor="capture:2",
            ),
            CapturedApiPage(
                page_number=2,
                records=(_teacher("teacher-1"),),
                next_cursor=None,
            ),
        )
    )
    materializer = ApiAuthorityMaterializer(
        Settings(upload_root=tmp_path / "uploads", _env_file=None),
        registry=_registry(adapter),
        fernet_key=key,
    )

    source = await materializer.materialize(
        session,
        task_id=task.id,
        api_source_id=api_source.id,
    )
    await session.commit()
    replayed = await materializer.materialize(
        session,
        task_id=task.id,
        api_source_id=api_source.id,
    )

    assert replayed.id == source.id
    assert adapter.capture_calls == 1
    artifact = anyio.Path(source.storage_path)
    assert await artifact.is_file()
    content = await artifact.read_text()
    assert "client_secret" not in content
    assert '"secret"' not in content
    lines = [json.loads(line) for line in content.splitlines()]
    assert lines[0]["record_type"] == "header"
    assert [line["external_id"] for line in lines[1:]] == [
        "teacher-1",
        "teacher-2",
    ]

    persisted = await session.get(ApiAuthoritySourceRecord, api_source.id)
    snapshot = await session.scalar(
        select(Snapshot).where(Snapshot.task_id == task.id)
    )
    assert persisted is not None and persisted.state == "ready"
    assert persisted.source_file_id == source.id
    assert persisted.snapshot_id == snapshot.id
    assert persisted.record_count == 2
    assert persisted.page_count == 2
    assert persisted.content_sha256 == source.sha256
    assert snapshot is not None and snapshot.source_role == "authoritative"


async def test_partial_api_capture_is_not_published(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    task, api_source = await _seed_source(session, fernet_key=key)
    await session.commit()
    adapter = FakeCaptureAdapter(
        (
            CapturedApiPage(
                page_number=1,
                records=(_teacher("teacher-1"),),
                next_cursor="capture:2",
            ),
        )
    )
    materializer = ApiAuthorityMaterializer(
        Settings(upload_root=tmp_path / "uploads", _env_file=None),
        registry=_registry(adapter),
        fernet_key=key,
    )

    with pytest.raises(ApiSourceFailure, match="connector_pagination_incomplete"):
        await materializer.materialize(
            session,
            task_id=task.id,
            api_source_id=api_source.id,
        )
    await session.commit()

    persisted = await session.get(ApiAuthoritySourceRecord, api_source.id)
    assert persisted is not None and persisted.state == "failed"
    assert persisted.safe_problem_code == "connector_pagination_incomplete"
    assert persisted.source_file_id is None
    assert await session.scalar(select(SourceFile)) is None
    assert await session.scalar(select(Snapshot)) is None
    artifacts = [
        path
        async for path in anyio.Path(tmp_path / "uploads" / "api-authority").glob(
            "*.jsonl"
        )
    ]
    assert not artifacts


async def test_duplicate_external_id_is_rejected_before_publication(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    task, api_source = await _seed_source(session, fernet_key=key)
    await session.commit()
    adapter = FakeCaptureAdapter(
        (
            CapturedApiPage(
                page_number=1,
                records=(
                    _teacher("duplicate", "第一条"),
                    _teacher("duplicate", "第二条"),
                ),
                next_cursor=None,
            ),
        )
    )
    materializer = ApiAuthorityMaterializer(
        Settings(upload_root=tmp_path / "uploads", _env_file=None),
        registry=_registry(adapter),
        fernet_key=key,
    )

    with pytest.raises(ApiSourceFailure, match="connector_duplicate_external_id"):
        await materializer.materialize(
            session,
            task_id=task.id,
            api_source_id=api_source.id,
        )

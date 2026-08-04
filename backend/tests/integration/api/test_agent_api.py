import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_reporting.rollback_cycles import AgentRollbackCycleService
from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.ai.providers.base import LLMRequest, LLMResponse
from app.api.dependencies import get_operator_context
from app.core.security import OperatorContext
from app.main import create_app
from app.models.agent_analysis import (
    AgentFindingRecord,
    AgentInputRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.models.agent_graph import AgentGraphRunRecord
from app.models.agent_runtime import (
    AgentConversationMessageRecord,
    AgentConversationRecord,
    AgentRunRecord,
    SchoolTaskLockRecord,
)
from app.models.reconciliation import ReconciliationTask
from app.models.remote_sources import RemoteSourceRecord
from app.models.snapshots import Snapshot, SourceFile
from tests.settings import build_test_settings


class ConversationProvider:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            output={
                "result": {
                    "kind": "start_confirmation",
                    "title": "本地学生同步",
                    "entity_types": ["student"],
                    "source_ref": "third-party/roster.csv",
                    "target_ref": "seewo/roster.csv",
                    "message_zh": "已确认两份本地数据。",
                }
            },
            provider="stub",
            model="stub",
        )


class SqlConversationProvider:
    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        del request
        return LLMResponse(
            output={
                "result": {
                    "kind": "start_confirmation",
                    "title": "SQL 全校数据同步",
                    "entity_types": ["department", "student", "teacher"],
                    "source_configuration_id": "authority-postgres",
                    "target_configuration_id": "seewo-mysql",
                    "message_zh": "已确认 PostgreSQL 权威来源和 MySQL 希沃目标。",
                }
            },
            provider="stub",
            model="stub",
        )


class InvalidConversationProvider:
    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        del request
        return LLMResponse(
            output={"unexpected": "shape"},
            provider="stub",
            model="stub",
        )


class BlockingConversationProvider:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        del request
        self.entered.set()
        await asyncio.to_thread(self.release.wait)
        return LLMResponse(
            output={
                "result": {
                    "kind": "start_confirmation",
                    "title": "不应覆盖活动任务的新确认",
                    "entity_types": ["student"],
                    "source_ref": "third-party/roster.csv",
                    "target_ref": "seewo/roster.csv",
                    "message_zh": "这条并发模型结果必须被丢弃。",
                }
            },
            provider="stub",
            model="stub",
        )


class IncrementalConversationProvider:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            output = {
                "result": {
                    "kind": "intent_update",
                    "title": "学生同步",
                    "entity_types": ["student"],
                    "message_zh": "已记住要同步学生，请继续选择数据来源。",
                }
            }
        else:
            output = {
                "result": {
                    "kind": "clarification",
                    "message_zh": "我会沿用上一轮已确认的学生范围。",
                }
            }
        return LLMResponse(output=output, provider="stub", model="stub")


class RemoteConversationProvider:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        evidence = json.loads(request.messages[1].content)["untrusted_evidence"]
        link_candidates = evidence.get("remote_link_candidates", [])
        remote_sources = evidence.get("available_remote_sources", [])
        remote_selection: dict[str, object]
        if link_candidates:
            selected = min(
                link_candidates,
                key=lambda item: int(item["end"]) - int(item["start"]),
            )
            remote_selection = {
                "remote_url_start": selected["start"],
                "remote_url_end": selected["end"],
            }
        else:
            remote_selection = {
                "remote_source_id": (
                    remote_sources[0]["remote_source_id"]
                    if remote_sources
                    else str(uuid4())
                )
            }
        return LLMResponse(
            output={
                "result": {
                    "kind": "start_confirmation",
                    "title": "网页学生同步",
                    "entity_types": ["student"],
                    **remote_selection,
                    "target_ref": "seewo/roster.csv",
                    "message_zh": "已登记网页数据，并确认希沃目标。",
                }
            },
            provider="stub",
            model="stub",
        )


@pytest.fixture
def agent_client(tmp_path: Path):
    settings = build_test_settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'agent-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        export_root=tmp_path / "exports",
        auto_create_schema=True,
        new_agent_enabled=True,
        new_agent_analysis_only=True,
        tokenization_secret="test-tokenization-secret",
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def graph_agent_client(tmp_path: Path):
    settings = build_test_settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'agent-graph-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        export_root=tmp_path / "exports",
        auto_create_schema=True,
        new_agent_enabled=True,
        agent_graph_enabled=True,
        new_agent_analysis_only=True,
        tokenization_secret="test-tokenization-secret",
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def graph_agent_v2_client(tmp_path: Path):
    settings = build_test_settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'agent-graph-v2-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        export_root=tmp_path / "exports",
        auto_create_schema=True,
        new_agent_enabled=True,
        agent_graph_enabled=True,
        source_ingestion_v2_enabled=True,
        new_agent_analysis_only=True,
        tokenization_secret="test-tokenization-secret",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _upload(client: TestClient, tmp_path: Path, role: str, name: str) -> str:
    path = tmp_path / name
    path.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,student@example.test\n",
        encoding="utf-8",
    )
    with path.open("rb") as handle:
        response = client.post(
            "/api/uploads",
            data={"source_role": role},
            files={"file": (name, handle, "text/csv")},
        )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_manual_csv_task_uses_agent_runtime_and_exposes_persisted_events(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(agent_client, tmp_path, "authoritative", "authority.csv")
    target_id = _upload(agent_client, tmp_path, "target", "target.csv")

    created = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "agent-manual-1"},
        json={
            "title": "全校学生同步",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "csv", "upload_id": target_id},
        },
    )

    assert created.status_code == 202, created.text
    task = created.json()
    assert task["workflow_version"] == "new-agent-v1"
    assert task["phase"] == "ingest_and_normalize"
    assert task["status"] == "running"
    assert task["title"] == "全校学生同步"

    history = agent_client.get("/api/agent/history")
    assert history.status_code == 200
    history_item = history.json()["items"][0]
    assert history_item["id"] == task["id"]
    assert history_item["completed_at"] is None
    assert history_item["target_source"] == {
        "key": history_item["target_source"]["key"],
        "name": "临时上传 · target.csv",
        "kind": "upload",
        "identified": True,
    }
    assert target_id.replace("-", "") not in history_item["target_source"]["key"]

    fetched = agent_client.get(f"/api/agent/tasks/{task['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == task["id"]

    events = agent_client.get(f"/api/agent/tasks/{task['id']}/events")
    assert events.status_code == 200
    body = events.json()
    assert body["cursor"] == "3"
    assert [event["type"] for event in body["events"]] == [
        "run.created",
        "school_lock.acquired",
        "phase.started",
    ]


def test_history_projects_live_findings_and_termination_request(
    graph_agent_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(
        graph_agent_client,
        tmp_path,
        "authoritative",
        "history-authority.csv",
    )
    target_id = _upload(
        graph_agent_client,
        tmp_path,
        "target",
        "history-target.csv",
    )
    created = graph_agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "history-live-facts"},
        json={
            "title": "实时历史事实",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "csv", "upload_id": target_id},
        },
    )
    assert created.status_code == 202, created.text
    task_id = UUID(created.json()["id"])

    async def seed_live_facts() -> None:
        async with graph_agent_client.app.state.database.session_factory() as session:
            run = await session.scalar(
                select(AgentRunRecord).where(AgentRunRecord.task_id == task_id)
            )
            graph = await session.scalar(
                select(AgentGraphRunRecord).where(AgentGraphRunRecord.run_id == run.id)
            )
            snapshots = {
                snapshot.source_role: snapshot
                for snapshot in await session.scalars(
                    select(Snapshot).where(Snapshot.task_id == task_id)
                )
            }
            assert run is not None
            assert graph is not None
            graph.termination_requested = True
            batch = AgentModelBatchRecord(
                run_id=run.id,
                task_id=task_id,
                tenant_id="school-1",
                entity_kind="student",
                input_hash=uuid4().hex * 2,
                item_count=2,
                status="completed",
                output_hash=uuid4().hex * 2,
            )
            session.add(batch)
            await session.flush()
            for ordinal in (1, 2):
                target_input = AgentInputRecord(
                    run_id=run.id,
                    task_id=task_id,
                    snapshot_id=snapshots["target"].id,
                    tenant_id="school-1",
                    source_role="target",
                    stable_locator=f"csv:{ordinal + 1}",
                    stable_order=ordinal,
                    entity_kind="student",
                    category="学生",
                    name=f"测试学生{ordinal}",
                    number=f"S-{ordinal}",
                    class_name="一班",
                    phone=f"1380000000{ordinal}",
                    email=f"student{ordinal}@example.test",
                    raw_row_number=ordinal + 1,
                    input_hash=uuid4().hex * 2,
                )
                session.add(target_input)
                await session.flush()
                work_item = AgentWorkItemRecord(
                    run_id=run.id,
                    task_id=task_id,
                    tenant_id="school-1",
                    source_snapshot_id=snapshots["authoritative"].id,
                    target_snapshot_id=snapshots["target"].id,
                    subject_input_id=target_input.id,
                    entity_kind="student",
                    kind="field_difference",
                    state="analyzed",
                    idempotency_hash=uuid4().hex * 2,
                    evidence_hash=uuid4().hex * 2,
                )
                session.add(work_item)
                await session.flush()
                session.add(
                    AgentFindingRecord(
                        run_id=run.id,
                        task_id=task_id,
                        work_item_id=work_item.id,
                        batch_id=batch.id,
                        kind="field_difference",
                        category_zh="字段不一致",
                        analysis_zh="字段需要治理。",
                        evidence_refs=[f"target:{ordinal}"],
                        content_hash=uuid4().hex * 2,
                    )
                )
            await session.commit()

    graph_agent_client.portal.call(seed_live_facts)

    history = graph_agent_client.get("/api/agent/history")

    assert history.status_code == 200, history.text
    item = next(row for row in history.json()["items"] if row["id"] == str(task_id))
    assert item["termination_requested"] is True
    assert item["issue_summary"]["total"] == 2


def test_graph_flag_routes_only_new_tasks_to_agent_graph_version(
    graph_agent_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(graph_agent_client, tmp_path, "authoritative", "graph-authority.csv")
    target_id = _upload(graph_agent_client, tmp_path, "target", "graph-target.csv")

    created = graph_agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "agent-graph-manual-1"},
        json={
            "title": "全校学生图同步",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "csv", "upload_id": target_id},
        },
    )

    assert created.status_code == 202, created.text
    assert created.json()["workflow_version"] == "agent-graph-v1"


def test_source_ingestion_v2_freezes_run_contract_versions(
    graph_agent_v2_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(
        graph_agent_v2_client,
        tmp_path,
        "authoritative",
        "graph-v2-authority.csv",
    )
    target_id = _upload(
        graph_agent_v2_client,
        tmp_path,
        "target",
        "graph-v2-target.csv",
    )

    created = graph_agent_v2_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "agent-graph-v2-contract"},
        json={
            "title": "全校学生图同步 V2",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "csv", "upload_id": target_id},
        },
    )

    assert created.status_code == 202, created.text

    async def load_run() -> AgentRunRecord:
        async with graph_agent_v2_client.app.state.database.session_factory() as session:
            run = await session.scalar(
                select(AgentRunRecord).where(AgentRunRecord.task_id == UUID(created.json()["id"]))
            )
            assert run is not None
            return run

    run = graph_agent_v2_client.portal.call(load_run)
    assert run.workflow_version == "agent-graph-v1"
    assert run.ingestion_contract_version == "source-ingestion-v2"
    assert run.execution_contract_version == "deterministic-execution-v2"


def test_agent_task_start_returns_stable_school_lock_conflict(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(agent_client, tmp_path, "authoritative", "authority-lock.csv")
    target_id = _upload(agent_client, tmp_path, "target", "target-lock.csv")
    intent = {
        "title": "全校学生同步",
        "entity_types": ["student"],
        "source": {"kind": "csv", "upload_id": source_id},
        "target": {"kind": "csv", "upload_id": target_id},
    }
    first = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "agent-lock-owner"},
        json=intent,
    )
    assert first.status_code == 202

    blocked = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "agent-lock-contender"},
        json=intent,
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "school_lock_conflict"
    assert blocked.json()["detail"]["owner_task_id"] == first.json()["id"]

    active_lock = agent_client.get("/api/agent/active-lock")
    assert active_lock.status_code == 200
    assert active_lock.json() == {
        "active": True,
        "owner_task_id": first.json()["id"],
        "owner_run_id": active_lock.json()["owner_run_id"],
        "acquired_at": active_lock.json()["acquired_at"],
        "heartbeat_at": active_lock.json()["heartbeat_at"],
    }


def test_agent_api_rejects_client_tenant_override(
    agent_client: TestClient,
) -> None:
    response = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "spoofed-agent-tenant"},
        json={
            "title": "越权任务",
            "tenant_id": "other-school",
            "entity_types": ["student"],
            "source": {"kind": "api", "configuration_id": "authority"},
            "target": {"kind": "api", "configuration_id": "target"},
        },
    )

    assert response.status_code == 422


def test_manual_api_rejects_non_csv_connector_before_task_and_lock_are_created(
    agent_client: TestClient,
) -> None:
    response = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "missing-configured-connector"},
        json={
            "title": "配置连接器同步",
            "entity_types": ["student"],
            "source": {"kind": "api", "configuration_id": "authority"},
            "target": {"kind": "database", "configuration_id": "seewo"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "manual_csv_only"
    assert agent_client.get("/api/agent/active-lock").json() == {
        "active": False,
        "owner_task_id": None,
        "owner_run_id": None,
        "acquired_at": None,
        "heartbeat_at": None,
    }
    assert agent_client.get("/api/agent/history").json()["items"] == []


def test_manual_uploaded_csv_can_start_a_mysql_target_task(tmp_path: Path) -> None:
    fixed_fields = {
        "category": "category",
        "name": "name",
        "number": "number",
        "class_name": "class_name",
        "phone": "phone",
        "email": "email",
    }
    settings = build_test_settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'csv-mysql-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        export_root=tmp_path / "exports",
        auto_create_schema=True,
        new_agent_enabled=True,
        agent_graph_enabled=True,
        source_ingestion_v3_enabled=True,
        agent_graph_sql_execution_enabled=True,
        new_agent_analysis_only=False,
        tokenization_secret="test-tokenization-secret",
        database_connector_configurations={
            "seewo-data-mysql": {
                "credential_reference": "secret://connectors/seewo-data-mysql",
                "dialect": "mysql",
                "table_name": "organization_people",
                "primary_key": "id",
                "version_column": "row_version",
                "field_columns": fixed_fields,
                "source_role": "target",
                "capabilities": {
                    "read": True,
                    "paginated": True,
                    "create": True,
                    "update": True,
                    "delete": True,
                    "optimistic_version": True,
                    "read_after_write": True,
                },
            }
        },
        database_connector_credentials={
            "secret://connectors/seewo-data-mysql": "mysql+asyncmy://hidden"
        },
    )
    with TestClient(create_app(settings)) as client:
        source_id = _upload(client, tmp_path, "authoritative", "students-to-mysql.csv")
        response = client.post(
            "/api/agent/tasks",
            headers={"Idempotency-Key": "manual-csv-to-mysql"},
            json={
                "title": "CSV 学生同步到希沃数据库",
                "entity_types": ["student"],
                "source": {"kind": "csv", "upload_id": source_id},
                "target": {
                    "kind": "database",
                    "configuration_id": "seewo-data-mysql",
                },
            },
        )

    assert response.status_code == 202, response.text
    assert response.json()["workflow_version"] == "agent-graph-v1"


def test_manual_api_rejects_remote_csv_before_task_and_lock_are_created(
    agent_client: TestClient,
) -> None:
    response = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "forged-manual-remote-source"},
        json={
            "title": "伪造网页来源",
            "entity_types": ["student"],
            "source": {
                "kind": "remote_csv",
                "remote_source_id": str(uuid4()),
            },
            "target": {
                "kind": "local",
                "source_ref": "seewo/roster.csv",
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "manual_csv_only"
    assert agent_client.get("/api/agent/active-lock").json()["active"] is False
    assert agent_client.get("/api/agent/history").json()["items"] == []


def test_manual_api_rejects_raw_url_before_task_and_lock_are_created(
    agent_client: TestClient,
) -> None:
    response = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "forged-manual-url"},
        json={
            "title": "伪造网页地址",
            "entity_types": ["student"],
            "source": {
                "kind": "csv",
                "upload_id": str(uuid4()),
                "url": "https://data.example.test/roster.csv",
            },
            "target": {"kind": "csv", "upload_id": str(uuid4())},
        },
    )

    assert response.status_code == 422
    assert agent_client.get("/api/agent/active-lock").json()["active"] is False
    assert agent_client.get("/api/agent/history").json()["items"] == []


def test_sql_pair_creates_durable_task_without_exposing_database_credentials(
    tmp_path: Path,
) -> None:
    fixed_fields = {
        "category": "category",
        "name": "name",
        "number": "number",
        "class_name": "class_name",
        "phone": "phone",
        "email": "email",
    }
    settings = build_test_settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'agent-sql-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        export_root=tmp_path / "exports",
        auto_create_schema=True,
        new_agent_enabled=True,
        agent_graph_enabled=True,
        source_ingestion_v2_enabled=True,
        agent_graph_sql_execution_enabled=True,
        new_agent_analysis_only=False,
        tokenization_secret="test-tokenization-secret",
        database_connector_configurations={
            "authority-postgres": {
                "credential_reference": "secret://connectors/authority-postgres",
                "dialect": "postgresql",
                "table_name": "organization_people",
                "primary_key": "id",
                "version_column": "row_version",
                "field_columns": fixed_fields,
                "source_role": "authoritative",
                "capabilities": {"read": True, "paginated": True},
            },
            "seewo-mysql": {
                "credential_reference": "secret://connectors/seewo-mysql",
                "dialect": "mysql",
                "table_name": "organization_people",
                "primary_key": "id",
                "version_column": "row_version",
                "field_columns": fixed_fields,
                "source_role": "target",
                "capabilities": {
                    "read": True,
                    "paginated": True,
                    "create": True,
                    "update": True,
                    "delete": True,
                    "optimistic_version": True,
                },
            },
        },
        database_connector_credentials={
            "secret://connectors/authority-postgres": (
                "postgresql+asyncpg://user:authority-secret@db/authority"
            ),
            "secret://connectors/seewo-mysql": ("mysql+asyncmy://user:target-secret@db/seewo"),
        },
    )
    with TestClient(create_app(settings)) as client:
        rejected_manual = client.post(
            "/api/agent/tasks",
            headers={"Idempotency-Key": "manual-sql-is-not-supported"},
            json={
                "title": "SQL 全校数据同步",
                "entity_types": ["department", "student", "teacher"],
                "source": {
                    "kind": "database",
                    "configuration_id": "authority-postgres",
                },
                "target": {
                    "kind": "database",
                    "configuration_id": "seewo-mysql",
                },
            },
        )
        assert rejected_manual.status_code == 422
        assert rejected_manual.json()["detail"]["code"] == "manual_csv_only"

        client.app.state.conversation_provider = SqlConversationProvider()
        conversation = client.post("/api/agent/conversations")
        message = client.post(
            f"/api/agent/conversations/{conversation.json()['id']}/messages",
            json={"message": "使用 PostgreSQL 权威库同步到 MySQL 希沃库"},
        )
        assert message.status_code == 200, message.text
        response = client.post(
            f"/api/agent/conversations/{conversation.json()['id']}/tasks",
            headers={"Idempotency-Key": "sql-postgres-to-mysql"},
            json={
                "title": "客户端不能覆盖 SQL 意图",
                "entity_types": ["student"],
                "source": {
                    "kind": "database",
                    "configuration_id": "seewo-mysql",
                },
                "target": {
                    "kind": "database",
                    "configuration_id": "authority-postgres",
                },
            },
        )

        assert response.status_code == 202, response.text
        task_id = UUID(response.json()["id"])
        history_item = client.get("/api/agent/history").json()["items"][0]
        assert history_item["target_source"] == {
            "key": history_item["target_source"]["key"],
            "name": "seewo-mysql",
            "kind": "database",
            "identified": True,
        }

        async def load_facts() -> tuple[ReconciliationTask, tuple[SourceFile, ...]]:
            async with client.app.state.database.session_factory() as session:
                task = await session.get(ReconciliationTask, task_id)
                assert task is not None
                sources = tuple(
                    await session.scalars(
                        select(SourceFile)
                        .where(SourceFile.task_id == task_id)
                        .order_by(SourceFile.source_role)
                    )
                )
                return task, sources

        task, sources = client.portal.call(load_facts)
        assert task.agent_intent["source"]["configuration_id"] == "authority-postgres"
        assert [item.source_role for item in sources] == ["authoritative", "target"]
        assert [item.storage_path for item in sources] == [
            "database://authority-postgres",
            "database://seewo-mysql",
        ]
        assert "authority-secret" not in response.text
        assert "target-secret" not in response.text


def test_sql_runtime_rejects_csv_database_mixed_pair_before_lock(
    graph_agent_v2_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(
        graph_agent_v2_client,
        tmp_path,
        "authoritative",
        "mixed-authority.csv",
    )

    response = graph_agent_v2_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "mixed-csv-sql"},
        json={
            "title": "不允许的混合数据源",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "database", "configuration_id": "seewo-mysql"},
        },
    )

    assert response.status_code == 422
    assert graph_agent_v2_client.get("/api/agent/active-lock").json()["active"] is False


def test_conversation_uses_model_discovered_local_sources(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "local-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,a@example.test\n",
            encoding="utf-8",
        )
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)
    provider = ConversationProvider()
    agent_client.app.state.conversation_provider = provider

    conversation = agent_client.post("/api/agent/conversations")
    message = agent_client.post(
        f"/api/agent/conversations/{conversation.json()['id']}/messages",
        json={"message": "同步本地学生数据"},
    )

    assert message.status_code == 200, message.text
    assert message.json()["message"] == "已确认两份本地数据。"
    assert message.json()["intent"]["source"] == {
        "kind": "local",
        "source_ref": "third-party/roster.csv",
    }
    evidence = json.loads(provider.requests[0].messages[1].content)[
        "untrusted_evidence"
    ]
    assert evidence["conversation_remote_csv_enabled"] is False
    assert "converse-school-data-sync@1.7.0" in provider.requests[0].messages[0].content

    created = agent_client.post(
        f"/api/agent/conversations/{conversation.json()['id']}/tasks",
        headers={"Idempotency-Key": "agent-local-conversation-1"},
        json={
            "title": "浏览器篡改标题",
            "entity_types": ["teacher"],
            "source": {"kind": "local", "source_ref": "third-party/other.csv"},
            "target": {"kind": "local", "source_ref": "seewo/other.csv"},
        },
    )

    assert created.status_code == 202, created.text
    assert created.json()["title"] == "本地学生同步"
    assert agent_client.get("/api/agent/history").json()["items"][0]["id"] == created.json()["id"]
    replay = agent_client.post(
        f"/api/agent/conversations/{conversation.json()['id']}/tasks",
        headers={"Idempotency-Key": "agent-local-conversation-1"},
        json={
            "title": "浏览器重放仍不应成为事实",
            "entity_types": ["department"],
            "source": {"kind": "local", "source_ref": "third-party/replayed.csv"},
            "target": {"kind": "local", "source_ref": "seewo/replayed.csv"},
        },
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == created.json()["id"]


def test_conversation_registers_one_remote_source_without_exposing_its_url(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "remote-conversation-sources"
    target = root / "seewo/roster.csv"
    target.parent.mkdir(parents=True)
    target.write_text("编号,姓名\nS001,张三\n", encoding="utf-8")
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )
    agent_client.app.state.settings.conversation_remote_csv_enabled = True
    provider = RemoteConversationProvider()
    agent_client.app.state.conversation_provider = provider
    conversation = agent_client.post("/api/agent/conversations").json()
    submitted_url = "https://data.example.test/roster.csv?secret=value"

    response = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": f"请同步 {submitted_url} 的学生"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["accepted_message"] == (
        "请同步 [远程CSV来源:data.example.test] 的学生"
    )
    assert response.json()["intent"]["source"]["kind"] == "remote_csv"
    assert response.json()["intent"]["source"]["display_origin"] == "data.example.test"
    remote_source_id = response.json()["intent"]["source"]["remote_source_id"]
    assert submitted_url not in response.text
    assert "secret=value" not in response.text
    request_text = "\n".join(
        message.content for message in provider.requests[0].messages
    )
    assert submitted_url not in request_text
    assert "secret=value" not in request_text
    assert "[远程CSV来源:data.example.test]" in provider.requests[0].messages[1].content
    evidence = json.loads(provider.requests[0].messages[1].content)[
        "untrusted_evidence"
    ]
    assert evidence["conversation_remote_csv_enabled"] is True
    assert evidence["remote_link_candidates"] == [
        {
            "start": 4,
            "end": 53,
            "display_url": "https://data.example.test/roster.csv?<redacted>",
            "trailing_text": " 的学生",
        }
    ]

    async def load_facts() -> tuple[RemoteSourceRecord, list[str]]:
        async with agent_client.app.state.database.session_factory() as session:
            remote = await session.scalar(
                select(RemoteSourceRecord).where(
                    RemoteSourceRecord.id == UUID(remote_source_id)
                )
            )
            messages = list(
                await session.scalars(
                    select(AgentConversationMessageRecord).order_by(
                        AgentConversationMessageRecord.created_at
                    )
                )
            )
            assert remote is not None
            return remote, [message.text for message in messages]

    remote, persisted_messages = agent_client.portal.call(load_facts)
    assert remote.original_url == submitted_url
    assert remote.display_origin == "data.example.test"
    assert persisted_messages[0] == "请同步 [远程CSV来源:data.example.test] 的学生"
    assert all(submitted_url not in message for message in persisted_messages)

    current = agent_client.get("/api/agent/conversations/current")
    assert current.status_code == 200, current.text
    current_source = current.json()["intent"]["source"]
    assert current_source["kind"] == "remote_csv"
    assert current_source["remote_source_id"] == remote_source_id
    assert current_source["display_origin"] == "data.example.test"
    assert submitted_url not in current.text
    assert "secret=value" not in current.text


def test_conversation_model_selects_csv_boundary_before_chinese_prose(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "remote-boundary-sources"
    target = root / "seewo/roster.csv"
    target.parent.mkdir(parents=True)
    target.write_text("编号,姓名\nS001,张三\n", encoding="utf-8")
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )
    agent_client.app.state.settings.conversation_remote_csv_enabled = True
    provider = RemoteConversationProvider()
    agent_client.app.state.conversation_provider = provider
    conversation = agent_client.post("/api/agent/conversations").json()
    submitted_url = "https://data.example.test/roster.csv"

    response = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": f"请同步{submitted_url}的数据"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["accepted_message"] == (
        "请同步[远程CSV来源:data.example.test]的数据"
    )
    remote_source_id = response.json()["intent"]["source"]["remote_source_id"]

    async def load_remote_source() -> RemoteSourceRecord | None:
        async with agent_client.app.state.database.session_factory() as session:
            return await session.scalar(
                select(RemoteSourceRecord).where(
                    RemoteSourceRecord.id == UUID(remote_source_id)
                )
            )

    remote = agent_client.portal.call(load_remote_source)
    assert remote is not None
    assert remote.original_url == submitted_url
    evidence = json.loads(provider.requests[0].messages[1].content)[
        "untrusted_evidence"
    ]
    assert [candidate["end"] for candidate in evidence["remote_link_candidates"]] == [
        39,
        42,
    ]


def test_conversation_link_registration_requires_one_link(
    agent_client: TestClient,
) -> None:
    agent_client.app.state.settings.conversation_remote_csv_enabled = True
    provider = RemoteConversationProvider()
    agent_client.app.state.conversation_provider = provider
    conversation = agent_client.post("/api/agent/conversations").json()

    response = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={
            "message": (
                "比较 https://one.example.test/a.csv "
                "和 https://two.example.test/b.csv"
            )
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "remote_source_multiple_links"
    assert provider.requests == []


def test_conversation_link_registration_rejects_non_https_url(
    agent_client: TestClient,
) -> None:
    agent_client.app.state.settings.conversation_remote_csv_enabled = True
    provider = RemoteConversationProvider()
    agent_client.app.state.conversation_provider = provider
    conversation = agent_client.post("/api/agent/conversations").json()

    response = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "同步 http://data.example.test/roster.csv"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "remote_source_https_required"
    assert provider.requests == []


def test_conversation_task_binds_its_remote_source_and_local_target(
    graph_agent_v2_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "remote-task-sources"
    target = root / "seewo/roster.csv"
    target.parent.mkdir(parents=True)
    target.write_text("编号,姓名\nS001,张三\n", encoding="utf-8")
    graph_agent_v2_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    graph_agent_v2_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )
    graph_agent_v2_client.app.state.settings.conversation_remote_csv_enabled = True
    graph_agent_v2_client.app.state.conversation_provider = RemoteConversationProvider()
    conversation = graph_agent_v2_client.post("/api/agent/conversations").json()
    message = graph_agent_v2_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "同步 https://data.example.test/roster.csv 的学生"},
    )
    assert message.status_code == 200, message.text
    remote_source_id = message.json()["intent"]["source"]["remote_source_id"]

    created = graph_agent_v2_client.post(
        f"/api/agent/conversations/{conversation['id']}/tasks",
        headers={"Idempotency-Key": "conversation-remote-task"},
        json={
            "title": "客户端内容应被忽略",
            "entity_types": ["teacher"],
            "source": {"kind": "csv", "upload_id": str(uuid4())},
            "target": {"kind": "csv", "upload_id": str(uuid4())},
        },
    )

    assert created.status_code == 202, created.text
    task_id = created.json()["id"]

    async def load_bindings() -> tuple[RemoteSourceRecord, ReconciliationTask, list[SourceFile]]:
        async with graph_agent_v2_client.app.state.database.session_factory() as session:
            remote = await session.scalar(
                select(RemoteSourceRecord).where(
                    RemoteSourceRecord.id == UUID(remote_source_id)
                )
            )
            task = await session.get(ReconciliationTask, UUID(task_id))
            files = list(
                await session.scalars(
                    select(SourceFile).where(SourceFile.task_id == UUID(task_id))
                )
            )
            assert remote is not None
            assert task is not None
            return remote, task, files

    remote, task, files = graph_agent_v2_client.portal.call(load_bindings)
    assert remote.task_id == UUID(task_id)
    assert remote.source_file_id is None
    assert task.agent_intent["source"] == {
        "kind": "remote_csv",
        "remote_source_id": remote_source_id,
        "upload_id": None,
        "configuration_id": None,
        "source_ref": None,
    }
    assert [source.source_role for source in files] == ["target"]


def test_conversation_task_rejects_remote_source_from_another_conversation(
    graph_agent_v2_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "cross-conversation-remote-sources"
    target = root / "seewo/roster.csv"
    target.parent.mkdir(parents=True)
    target.write_text("编号,姓名\nS001,张三\n", encoding="utf-8")
    graph_agent_v2_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    graph_agent_v2_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )
    graph_agent_v2_client.app.state.settings.conversation_remote_csv_enabled = True
    graph_agent_v2_client.app.state.conversation_provider = RemoteConversationProvider()
    owner = graph_agent_v2_client.post("/api/agent/conversations").json()
    owner_message = graph_agent_v2_client.post(
        f"/api/agent/conversations/{owner['id']}/messages",
        json={"message": "同步 https://data.example.test/roster.csv 的学生"},
    )
    remote_source_id = owner_message.json()["intent"]["source"]["remote_source_id"]

    async def create_foreign_intent() -> UUID:
        async with graph_agent_v2_client.app.state.database.session_factory() as session:
            conversation = await AgentRuntimeRepository(session).create_conversation(
                tenant_id="school-1",
                created_by="operator-1",
            )
            conversation.context = {
                "title": "越权远程同步",
                "entity_types": ["student"],
                "source": {
                    "kind": "remote_csv",
                    "remote_source_id": remote_source_id,
                },
                "target": {
                    "kind": "local",
                    "source_ref": "seewo/roster.csv",
                },
                "decision_kind": "start_confirmation",
            }
            await session.commit()
            return conversation.id

    foreign_conversation_id = graph_agent_v2_client.portal.call(create_foreign_intent)
    response = graph_agent_v2_client.post(
        f"/api/agent/conversations/{foreign_conversation_id}/tasks",
        headers={"Idempotency-Key": "cross-conversation-remote-task"},
        json={
            "title": "客户端内容应被忽略",
            "entity_types": ["teacher"],
            "source": {"kind": "csv", "upload_id": str(uuid4())},
            "target": {"kind": "csv", "upload_id": str(uuid4())},
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "resource_not_found"
    assert graph_agent_v2_client.get("/api/agent/active-lock").json()["active"] is False


def test_conversation_without_link_does_not_register_remote_source(
    agent_client: TestClient,
) -> None:
    agent_client.app.state.settings.conversation_remote_csv_enabled = True
    agent_client.app.state.conversation_provider = IncrementalConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()

    response = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "我要同步学生"},
    )
    assert response.status_code == 200, response.text

    async def count_remote_sources() -> int:
        async with agent_client.app.state.database.session_factory() as session:
            return len(list(await session.scalars(select(RemoteSourceRecord))))

    assert agent_client.portal.call(count_remote_sources) == 0


def test_local_source_api_returns_only_safe_server_capabilities(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "listed-local-sources"
    for relative in ("data/authority.csv", "seewo/target.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("编号,姓名\n001,测试", encoding="utf-8")
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)

    response = agent_client.get("/api/agent/local-sources")

    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "source_ref": "data/authority.csv",
            "kind": "csv",
            "writable_as_target": False,
        },
        {
            "source_ref": "seewo/target.csv",
            "kind": "csv",
            "writable_as_target": True,
        },
    ]
    assert str(root) not in response.text


def test_local_task_rejects_target_outside_server_write_roots(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "read-only-local-target"
    for relative in ("data/authority.csv", "readonly/target.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("编号,姓名\n001,测试", encoding="utf-8")
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)

    response = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "read-only-local-target"},
        json={
            "title": "不应启动的本地任务",
            "entity_types": ["student"],
            "source": {"kind": "local", "source_ref": "data/authority.csv"},
            "target": {"kind": "local", "source_ref": "readonly/target.csv"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_agent_intent"
    assert agent_client.get("/api/agent/active-lock").json()["active"] is False


def test_uploaded_authority_can_bind_to_a_writable_local_target(
    graph_agent_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(
        graph_agent_client,
        tmp_path,
        "authoritative",
        "uploaded-authority.csv",
    )
    root = tmp_path / "mixed-local-target"
    target = root / "seewo/target.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000002,student@example.test\n",
        encoding="utf-8",
    )
    graph_agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    graph_agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)

    response = graph_agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "uploaded-authority-local-target"},
        json={
            "title": "直接写回希沃原文件",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "local", "source_ref": "seewo/target.csv"},
        },
    )

    assert response.status_code == 202, response.text
    task_id = response.json()["id"]

    async def bound_files() -> list[tuple[str, str, bool]]:
        async with graph_agent_client.app.state.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        Snapshot.source_role,
                        SourceFile.storage_path,
                        SourceFile.managed_storage,
                    )
                    .join(SourceFile, SourceFile.id == Snapshot.source_file_id)
                    .where(Snapshot.task_id == UUID(task_id))
                    .order_by(Snapshot.source_role)
                )
            ).all()
            return [
                (str(role), str(path), bool(managed_storage))
                for role, path, managed_storage in rows
            ]

    files = graph_agent_client.portal.call(bound_files)
    assert [role for role, _path, _managed in files] == ["authoritative", "target"]
    assert files[1] == ("target", str(target), False)

    second_source_id = _upload(
        graph_agent_client,
        tmp_path,
        "authoritative",
        "second-uploaded-authority.csv",
    )
    graph_agent_client.app.dependency_overrides[get_operator_context] = lambda: OperatorContext(
        operator_id="second-demo-operator",
        tenant_id="second-demo-school",
    )
    repeated = graph_agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "repeat-same-local-target"},
        json={
            "title": "再次同步同一个希沃原文件",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": second_source_id},
            "target": {"kind": "local", "source_ref": "seewo/target.csv"},
        },
    )

    assert repeated.status_code == 202, repeated.text


def test_local_task_requires_explicit_acceptance_when_target_changed_outside_agent(
    graph_agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "drifted-local-target"
    authority = root / "data/authority.csv"
    target = root / "seewo/target.csv"
    authority.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    authority.write_text("编号,姓名\n001,新姓名\n", encoding="utf-8")
    published_content = "编号,姓名\n001,新姓名\n"
    target.write_text(published_content, encoding="utf-8")
    graph_agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    graph_agent_client.app.state.settings.agent_local_write_roots = (
        (root / "seewo").resolve(),
    )

    async def seed_last_successful_sync() -> None:
        async with graph_agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="completed",
                stage="terminal",
                workflow_version="agent-graph-v1",
                task_kind="sync",
                title="上一次成功同步",
                agent_intent={
                    "target": {
                        "kind": "local",
                        "source_ref": "seewo/target.csv",
                    }
                },
                idempotency_key=f"drift-source-{uuid4()}",
                request_hash="d" * 64,
            )
            session.add(task)
            await session.flush()
            await AgentReportingService(session).generate(
                task_id=task.id,
                tenant_id=task.tenant_id,
                kind="sync",
                terminal_state="completed",
                facts={
                    "mutations": [{
                        "id": str(uuid4()),
                        "status": "succeeded",
                        "verification": {"valid": True},
                    }],
                    "publication": {
                        "status": "published",
                        "source_ref": "seewo/target.csv",
                        "published_sha256": hashlib.sha256(
                            published_content.encode()
                        ).hexdigest(),
                    },
                },
            )
            await session.commit()

    graph_agent_client.portal.call(seed_last_successful_sync)
    target.write_text("编号,姓名\n001,旧姓名\n", encoding="utf-8")
    intent = {
        "title": "再次同步",
        "entity_types": ["student"],
        "source": {"kind": "local", "source_ref": "data/authority.csv"},
        "target": {"kind": "local", "source_ref": "seewo/target.csv"},
    }

    blocked = graph_agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "drifted-local-target"},
        json=intent,
    )

    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "target_baseline_drift"
    assert blocked.json()["detail"]["source_ref"] == "seewo/target.csv"

    accepted = graph_agent_client.post(
        "/api/agent/tasks",
        headers={
            "Idempotency-Key": "drifted-local-target",
            "X-Accept-Current-Target-Baseline": "true",
        },
        json=intent,
    )

    assert accepted.status_code == 202, accepted.text

    async def accepted_baseline() -> str | None:
        async with graph_agent_client.app.state.database.session_factory() as session:
            task = await session.get(
                ReconciliationTask,
                UUID(accepted.json()["id"]),
            )
            assert task is not None
            return (task.agent_intent or {}).get(
                "accepted_target_baseline_sha256"
            )

    assert graph_agent_client.portal.call(accepted_baseline) == hashlib.sha256(
        target.read_bytes()
    ).hexdigest()


def test_conversation_returns_sanitized_error_for_invalid_model_output(
    agent_client: TestClient,
) -> None:
    agent_client.app.state.conversation_provider = InvalidConversationProvider()
    conversation = agent_client.post("/api/agent/conversations")

    response = agent_client.post(
        f"/api/agent/conversations/{conversation.json()['id']}/messages",
        json={"message": "你是谁"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "conversation_model_error",
        "message": "对话模型暂时无法生成有效回复，请稍后重试。",
    }


def test_current_conversation_restores_persisted_messages_and_active_task(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "resumable-local-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,a@example.test\n",
            encoding="utf-8",
        )
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)
    agent_client.app.state.conversation_provider = ConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()

    message = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "同步本地学生数据"},
    )
    assert message.status_code == 200, message.text
    created = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/tasks",
        headers={"Idempotency-Key": "resumable-conversation-task"},
        json={
            "title": "客户端不会成为事实",
            "entity_types": ["teacher"],
            "source": {"kind": "local", "source_ref": "third-party/other.csv"},
            "target": {"kind": "local", "source_ref": "seewo/other.csv"},
        },
    )
    assert created.status_code == 202, created.text

    current = agent_client.get("/api/agent/conversations/current")

    assert current.status_code == 200, current.text
    body = current.json()
    assert body["id"] == conversation["id"]
    assert [(item["role"], item["text"]) for item in body["messages"]] == [
        ("user", "同步本地学生数据"),
        ("assistant", "已确认两份本地数据。"),
    ]
    assert body["intent"]["title"] == "本地学生同步"
    assert body["task"]["id"] == created.json()["id"]
    assert body["start_confirmation"] is None


def test_started_conversation_does_not_restore_confirmation_after_task_failure(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "failed-conversation-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,a@example.test\n",
            encoding="utf-8",
        )
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)
    agent_client.app.state.conversation_provider = ConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()
    sent = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "同步本地学生数据"},
    )
    assert sent.status_code == 200, sent.text
    created = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/tasks",
        headers={"Idempotency-Key": "failed-conversation-task"},
        json={
            "title": "客户端不会成为事实",
            "entity_types": ["teacher"],
            "source": {"kind": "local", "source_ref": "third-party/other.csv"},
            "target": {"kind": "local", "source_ref": "seewo/other.csv"},
        },
    )
    assert created.status_code == 202, created.text

    async def fail_task() -> str:
        async with agent_client.app.state.database.session_factory() as session:
            task = await session.get(ReconciliationTask, UUID(created.json()["id"]))
            run = await session.scalar(
                select(AgentRunRecord).where(AgentRunRecord.task_id == task.id)
            )
            lock = await session.scalar(
                select(SchoolTaskLockRecord).where(
                    SchoolTaskLockRecord.owner_task_id == task.id,
                    SchoolTaskLockRecord.active.is_(True),
                )
            )
            assert task is not None
            assert run is not None
            assert lock is not None
            task.status = "failed"
            run.status = "failed"
            lock.active = False
            conversation_record = await session.get(
                AgentConversationRecord,
                UUID(conversation["id"]),
            )
            assert conversation_record is not None
            decision_kind = str(conversation_record.context.get("decision_kind"))
            await session.commit()
            return decision_kind

    decision_kind = agent_client.portal.call(fail_task)
    current = agent_client.get("/api/agent/conversations/current")

    assert decision_kind == "task_started"
    assert current.status_code == 200
    assert current.json()["task"]["status"] == "failed"
    assert current.json()["start_confirmation"] is None


def test_terminal_conversation_hides_the_old_task_and_restores_the_next_confirmation(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "terminal-conversation-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,a@example.test\n",
            encoding="utf-8",
        )
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)
    agent_client.app.state.conversation_provider = ConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()
    first_message = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "同步本地学生数据"},
    )
    assert first_message.status_code == 200, first_message.text
    created = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/tasks",
        headers={"Idempotency-Key": "terminal-conversation-first-task"},
        json={
            "title": "客户端不会成为事实",
            "entity_types": ["teacher"],
            "source": {"kind": "local", "source_ref": "third-party/other.csv"},
            "target": {"kind": "local", "source_ref": "seewo/other.csv"},
        },
    )
    assert created.status_code == 202, created.text

    async def complete_task() -> None:
        async with agent_client.app.state.database.session_factory() as session:
            task = await session.get(ReconciliationTask, UUID(created.json()["id"]))
            run = await session.scalar(
                select(AgentRunRecord).where(AgentRunRecord.task_id == task.id)
            )
            lock = await session.scalar(
                select(SchoolTaskLockRecord).where(
                    SchoolTaskLockRecord.owner_task_id == task.id,
                    SchoolTaskLockRecord.active.is_(True),
                )
            )
            assert task is not None
            assert run is not None
            assert lock is not None
            task.status = "completed"
            run.status = "completed"
            run.updated_at = datetime.now(UTC)
            lock.active = False
            await session.commit()

    agent_client.portal.call(complete_task)
    next_message = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "再同步一次学生数据"},
    )
    assert next_message.status_code == 200, next_message.text

    current = agent_client.get("/api/agent/conversations/current")

    assert current.status_code == 200, current.text
    assert current.json()["task"] is None
    assert current.json()["start_confirmation"] == {
        "title": "本地学生同步",
        "summary": "已确认两份本地数据。",
        "entity_types": ["student"],
    }
    second_task = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/tasks",
        headers={"Idempotency-Key": "terminal-conversation-second-task"},
        json={
            "title": "仍以服务端确认意图为准",
            "entity_types": ["teacher"],
            "source": {"kind": "local", "source_ref": "third-party/other.csv"},
            "target": {"kind": "local", "source_ref": "seewo/other.csv"},
        },
    )
    assert second_task.status_code == 202, second_task.text
    assert second_task.json()["id"] != created.json()["id"]


def test_message_result_cannot_overwrite_a_task_started_during_model_work(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "concurrent-conversation-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,a@example.test\n",
            encoding="utf-8",
        )
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)
    agent_client.app.state.conversation_provider = ConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()
    prepared = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "同步本地学生数据"},
    )
    assert prepared.status_code == 200, prepared.text

    blocking_provider = BlockingConversationProvider()
    agent_client.app.state.conversation_provider = blocking_provider
    with ThreadPoolExecutor(max_workers=2) as executor:
        message_future = executor.submit(
            agent_client.post,
            f"/api/agent/conversations/{conversation['id']}/messages",
            json={"message": "模型处理期间尝试启动旧确认"},
        )
        assert blocking_provider.entered.wait(timeout=2)
        start_future = executor.submit(
            agent_client.post,
            f"/api/agent/conversations/{conversation['id']}/tasks",
            headers={"Idempotency-Key": "concurrent-conversation-task"},
            json={
                "title": "客户端不会成为事实",
                "entity_types": ["teacher"],
                "source": {"kind": "local", "source_ref": "third-party/other.csv"},
                "target": {"kind": "local", "source_ref": "seewo/other.csv"},
            },
        )
        try:
            started = start_future.result(timeout=1)
        except FutureTimeoutError:
            blocking_provider.release.set()
            started = start_future.result(timeout=5)
        else:
            blocking_provider.release.set()
        message_response = message_future.result(timeout=5)

    assert started.status_code == 202, started.text
    assert message_response.status_code == 409, message_response.text
    assert message_response.json()["detail"]["code"] == "invalid_state"
    current = agent_client.get("/api/agent/conversations/current")
    assert current.status_code == 200
    assert current.json()["task"]["id"] == started.json()["id"]
    assert current.json()["start_confirmation"] is None
    assert all(
        message["text"] != "这条并发模型结果必须被丢弃。"
        for message in current.json()["messages"]
    )


def test_conversation_rejects_a_second_message_while_model_work_is_in_flight(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "concurrent-message-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,a@example.test\n",
            encoding="utf-8",
        )
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)
    agent_client.app.state.conversation_provider = ConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()
    prepared = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "同步本地学生数据"},
    )
    assert prepared.status_code == 200, prepared.text

    blocking_provider = BlockingConversationProvider()
    agent_client.app.state.conversation_provider = blocking_provider
    with ThreadPoolExecutor(max_workers=1) as executor:
        first_future = executor.submit(
            agent_client.post,
            f"/api/agent/conversations/{conversation['id']}/messages",
            json={"message": "继续完善当前同步要求"},
        )
        assert blocking_provider.entered.wait(timeout=2)
        try:
            second_response = agent_client.post(
                f"/api/agent/conversations/{conversation['id']}/messages",
                json={"message": "这条并发消息不应进入对话"},
            )
        finally:
            blocking_provider.release.set()
        first_response = first_future.result(timeout=5)

    assert second_response.status_code == 409, second_response.text
    assert second_response.json()["detail"]["code"] == "conversation_busy"
    assert first_response.status_code == 200, first_response.text
    current = agent_client.get("/api/agent/conversations/current")
    assert current.status_code == 200
    assert current.json()["start_confirmation"]["title"] == "不应覆盖活动任务的新确认"
    assert all(
        message["text"] != "这条并发消息不应进入对话"
        for message in current.json()["messages"]
    )


def test_current_conversation_ignores_first_message_claim_while_model_is_in_flight(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "in-flight-current-conversation-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,a@example.test\n",
            encoding="utf-8",
        )
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)
    blocking_provider = BlockingConversationProvider()
    agent_client.app.state.conversation_provider = blocking_provider
    conversation = agent_client.post("/api/agent/conversations").json()

    with ThreadPoolExecutor(max_workers=1) as executor:
        message_future = executor.submit(
            agent_client.post,
            f"/api/agent/conversations/{conversation['id']}/messages",
            json={"message": "同步本地学生数据"},
        )
        assert blocking_provider.entered.wait(timeout=2)
        read_client = TestClient(agent_client.app, raise_server_exceptions=False)
        try:
            current = read_client.get("/api/agent/conversations/current")
        finally:
            read_client.close()
            blocking_provider.release.set()
        message_response = message_future.result(timeout=5)

    assert current.status_code == 200, current.text
    assert current.json()["intent"] is None
    assert message_response.status_code == 200, message_response.text


def test_conversation_recovers_an_abandoned_message_claim(
    agent_client: TestClient,
) -> None:
    agent_client.app.state.conversation_provider = ConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()

    async def abandon_claim() -> None:
        async with agent_client.app.state.database.session_factory() as session:
            record = await session.get(
                AgentConversationRecord,
                UUID(conversation["id"]),
            )
            assert record is not None
            record.context = {
                "_message_in_flight": {
                    "token": "abandoned-worker",
                    "claimed_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                }
            }
            await session.commit()

    agent_client.portal.call(abandon_claim)
    recovered = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "继续同步本地学生数据"},
    )

    assert recovered.status_code == 200, recovered.text
    current = agent_client.get("/api/agent/conversations/current")
    assert current.status_code == 200
    assert any(
        message["text"] == "继续同步本地学生数据"
        for message in current.json()["messages"]
    )

    async def load_context() -> dict[str, object]:
        async with agent_client.app.state.database.session_factory() as session:
            record = await session.get(
                AgentConversationRecord,
                UUID(conversation["id"]),
            )
            assert record is not None
            return record.context

    assert "_message_in_flight" not in agent_client.portal.call(load_context)


def test_conversation_timeout_clears_claim_and_persists_retryable_error(
    agent_client: TestClient,
) -> None:
    agent_client.app.state.settings.conversation_model_timeout_seconds = 0.05
    agent_client.app.state.settings.conversation_message_lease_seconds = 0.2
    blocking_provider = BlockingConversationProvider()
    agent_client.app.state.conversation_provider = blocking_provider
    conversation = agent_client.post("/api/agent/conversations").json()

    with ThreadPoolExecutor(max_workers=1) as executor:
        message_future = executor.submit(
            agent_client.post,
            f"/api/agent/conversations/{conversation['id']}/messages",
            json={"message": "超时后仍应可以重试"},
        )
        assert blocking_provider.entered.wait(timeout=2)
        try:
            response = message_future.result(timeout=3)
        finally:
            blocking_provider.release.set()

    assert response.status_code == 504, response.text
    assert response.json()["detail"] == {
        "code": "conversation_model_timeout",
        "message": "对话理解超时，请稍后重试。",
    }
    current = agent_client.get("/api/agent/conversations/current")
    assert current.status_code == 200, current.text
    assert current.json()["messages"][-1]["kind"] == "error"
    assert current.json()["messages"][-1]["text"] == "对话理解超时，请稍后重试。"

    async def load_context() -> dict[str, object]:
        async with agent_client.app.state.database.session_factory() as session:
            record = await session.get(AgentConversationRecord, UUID(conversation["id"]))
            assert record is not None
            return record.context

    assert "_message_in_flight" not in agent_client.portal.call(load_context)

    agent_client.app.state.conversation_provider = ConversationProvider()
    retry = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "再次尝试同步"},
    )
    assert retry.status_code == 200, retry.text


def test_current_conversation_restores_an_unstarted_confirmation(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "confirmation-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("类别,姓名,编号,班级,电话,邮箱\n", encoding="utf-8")
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)
    agent_client.app.state.conversation_provider = ConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()
    sent = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "同步本地学生数据"},
    )
    assert sent.status_code == 200, sent.text

    current = agent_client.get("/api/agent/conversations/current")

    assert current.status_code == 200
    assert current.json()["task"] is None
    assert current.json()["start_confirmation"] == {
        "title": "本地学生同步",
        "summary": "已确认两份本地数据。",
        "entity_types": ["student"],
    }


def test_current_conversation_is_hidden_from_another_tenant(
    agent_client: TestClient,
) -> None:
    created = agent_client.post("/api/agent/conversations")
    assert created.status_code == 201

    agent_client.app.dependency_overrides[get_operator_context] = lambda: OperatorContext(
        operator_id="demo-operator",
        tenant_id="other-school",
    )
    try:
        current = agent_client.get("/api/agent/conversations/current")
    finally:
        agent_client.app.dependency_overrides.pop(get_operator_context, None)

    assert current.status_code == 200
    assert current.json() is None


def test_current_conversation_prioritizes_the_school_lock_owner_over_a_new_empty_chat(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "locked-conversation-sources"
    for relative in ("third-party/roster.csv", "seewo/roster.csv"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "类别,姓名,编号,班级,电话,邮箱\n学生,张三,S001,一班,13800000001,a@example.test\n",
            encoding="utf-8",
        )
    agent_client.app.state.settings.agent_local_read_roots = (root.resolve(),)
    agent_client.app.state.settings.agent_local_write_roots = ((root / "seewo").resolve(),)
    agent_client.app.state.conversation_provider = ConversationProvider()
    owner = agent_client.post("/api/agent/conversations").json()
    message = agent_client.post(
        f"/api/agent/conversations/{owner['id']}/messages",
        json={"message": "同步本地学生数据"},
    )
    assert message.status_code == 200, message.text
    started = agent_client.post(
        f"/api/agent/conversations/{owner['id']}/tasks",
        headers={"Idempotency-Key": "locked-conversation-owner"},
        json={
            "title": "客户端草案不作为事实",
            "entity_types": ["teacher"],
            "source": {"kind": "local", "source_ref": "third-party/other.csv"},
            "target": {"kind": "local", "source_ref": "seewo/other.csv"},
        },
    )
    assert started.status_code == 202, started.text

    newer_empty = agent_client.post("/api/agent/conversations")
    assert newer_empty.status_code == 201

    current = agent_client.get("/api/agent/conversations/current")

    assert current.status_code == 200
    assert current.json()["id"] == owner["id"]
    assert current.json()["task"]["id"] == started.json()["id"]


def test_conversation_reset_deletes_chat_but_preserves_completed_task_facts(
    agent_client: TestClient,
) -> None:
    agent_client.app.state.conversation_provider = IncrementalConversationProvider()
    first = agent_client.post("/api/agent/conversations").json()
    first_message = agent_client.post(
        f"/api/agent/conversations/{first['id']}/messages",
        json={"message": "第一段旧对话"},
    )
    assert first_message.status_code == 200, first_message.text
    second = agent_client.post("/api/agent/conversations").json()
    second_message = agent_client.post(
        f"/api/agent/conversations/{second['id']}/messages",
        json={"message": "第二段旧对话"},
    )
    assert second_message.status_code == 200, second_message.text

    async def seed_completed_run() -> tuple[UUID, UUID]:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                workflow_version="new-agent-v1",
                status="completed",
                stage="terminal",
                idempotency_key=f"completed-conversation-{uuid4()}",
                request_hash="c" * 64,
            )
            session.add(task)
            await session.flush()
            run = AgentRunRecord(
                task_id=task.id,
                conversation_id=UUID(first["id"]),
                tenant_id="school-1",
                kind="sync",
                workflow_version="new-agent-v1",
                phase="generate_report",
                status="completed",
                version=1,
                attempt_count=0,
            )
            session.add(run)
            await session.commit()
            return task.id, run.id

    task_id, run_id = agent_client.portal.call(seed_completed_run)

    reset = agent_client.post(
        "/api/agent/conversations/current/reset",
        headers={"Idempotency-Key": "new-conversation-1"},
        json={},
    )

    assert reset.status_code == 201, reset.text
    new_conversation_id = UUID(reset.json()["id"])
    assert new_conversation_id not in {UUID(first["id"]), UUID(second["id"])}

    repeated = agent_client.post(
        "/api/agent/conversations/current/reset",
        headers={"Idempotency-Key": "new-conversation-1"},
        json={},
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == str(new_conversation_id)

    async def inspect_reset() -> tuple[list[UUID], int, UUID | None, bool]:
        async with agent_client.app.state.database.session_factory() as session:
            conversations = list(
                await session.scalars(
                    select(AgentConversationRecord).where(
                        AgentConversationRecord.tenant_id == "school-1",
                        AgentConversationRecord.created_by == "demo-operator",
                    )
                )
            )
            messages = list(await session.scalars(select(AgentConversationMessageRecord)))
            run = await session.get(AgentRunRecord, run_id)
            task = await session.get(ReconciliationTask, task_id)
            return (
                [conversation.id for conversation in conversations],
                len(messages),
                run.conversation_id if run is not None else None,
                task is not None,
            )

    conversation_ids, message_count, run_conversation_id, task_exists = agent_client.portal.call(
        inspect_reset
    )
    assert conversation_ids == [new_conversation_id]
    assert message_count == 0
    assert run_conversation_id is None
    assert task_exists is True


def test_conversation_reset_is_blocked_by_active_school_task(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    conversation = agent_client.post("/api/agent/conversations")
    assert conversation.status_code == 201
    source_id = _upload(agent_client, tmp_path, "authoritative", "reset-lock-source.csv")
    target_id = _upload(agent_client, tmp_path, "target", "reset-lock-target.csv")
    task = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "reset-lock-owner"},
        json={
            "title": "占用学校锁的任务",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "csv", "upload_id": target_id},
        },
    )
    assert task.status_code == 202, task.text

    reset = agent_client.post(
        "/api/agent/conversations/current/reset",
        headers={"Idempotency-Key": "blocked-new-conversation"},
        json={},
    )

    assert reset.status_code == 409
    assert reset.json()["detail"] == {
        "code": "conversation_active_task",
        "message": "当前学校仍有任务正在处理，请先完成或终止任务",
        "owner_task_id": task.json()["id"],
    }
    current = agent_client.get("/api/agent/conversations/current")
    assert current.json()["id"] == conversation.json()["id"]


def test_failed_conversation_reply_is_persisted_as_recoverable_message(
    agent_client: TestClient,
) -> None:
    agent_client.app.state.conversation_provider = InvalidConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()

    response = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "你是谁"},
    )
    assert response.status_code == 502

    current = agent_client.get("/api/agent/conversations/current")

    assert [(item["role"], item["kind"]) for item in current.json()["messages"]] == [
        ("user", "normal"),
        ("assistant", "error"),
    ]
    assert "稍后重试" in current.json()["messages"][-1]["text"]


def test_conversation_model_history_excludes_assistant_error_messages(
    agent_client: TestClient,
) -> None:
    agent_client.app.state.conversation_provider = InvalidConversationProvider()
    conversation = agent_client.post("/api/agent/conversations").json()
    failed = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "第一次问题"},
    )
    assert failed.status_code == 502

    provider = IncrementalConversationProvider()
    agent_client.app.state.conversation_provider = provider
    recovered = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "第二次问题"},
    )
    assert recovered.status_code == 200, recovered.text

    evidence = json.loads(provider.requests[0].messages[1].content)[
        "untrusted_evidence"
    ]
    assert evidence["history"] == [
        {"role": "user", "kind": "normal", "text": "第一次问题"},
        {"role": "user", "kind": "normal", "text": "第二次问题"},
    ]


def test_conversation_model_receives_complete_persisted_history(
    agent_client: TestClient,
) -> None:
    provider = IncrementalConversationProvider()
    agent_client.app.state.conversation_provider = provider
    conversation = agent_client.post("/api/agent/conversations").json()

    first = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "我要同步学生"},
    )
    assert first.status_code == 200, first.text

    second = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "继续选择数据来源"},
    )
    assert second.status_code == 200, second.text
    current = agent_client.get("/api/agent/conversations/current")
    assert current.json()["intent"]["entity_types"] == ["student"]
    assert current.json()["intent"]["title"] == "学生同步"

    evidence = json.loads(provider.requests[1].messages[1].content)["untrusted_evidence"]
    assert evidence["current_intent"]["entity_types"] == ["student"]
    assert evidence["current_intent"]["title"] == "学生同步"
    assert evidence["history"] == [
        {
            "role": "user",
            "kind": "normal",
            "text": "我要同步学生",
        },
        {
            "role": "assistant",
            "kind": "normal",
            "text": "已记住要同步学生，请继续选择数据来源。",
        },
        {
            "role": "user",
            "kind": "normal",
            "text": "继续选择数据来源",
        },
    ]


def test_conversation_context_limit_preserves_user_message_without_calling_model(
    agent_client: TestClient,
) -> None:
    provider = ConversationProvider()
    agent_client.app.state.conversation_provider = provider
    agent_client.app.state.settings.conversation_context_max_tokens = 100
    agent_client.app.state.settings.conversation_context_reserved_output_tokens = 20
    conversation = agent_client.post("/api/agent/conversations").json()

    response = agent_client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"message": "我要同步学生"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "conversation_context_limit"
    assert response.json()["detail"]["message"] == ("当前对话内容已达到模型处理上限，请开启新对话")
    assert provider.requests == []
    current = agent_client.get("/api/agent/conversations/current")
    assert [(item["role"], item["kind"], item["text"]) for item in current.json()["messages"]] == [
        ("user", "normal", "我要同步学生")
    ]


def test_termination_persists_history_before_releasing_school_lock(
    agent_client: TestClient,
    tmp_path: Path,
) -> None:
    source_id = _upload(agent_client, tmp_path, "authoritative", "terminate-source.csv")
    target_id = _upload(agent_client, tmp_path, "target", "terminate-target.csv")
    created = agent_client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "agent-terminate-1"},
        json={
            "title": "待终止同步",
            "entity_types": ["student"],
            "source": {"kind": "csv", "upload_id": source_id},
            "target": {"kind": "csv", "upload_id": target_id},
        },
    )
    task_id = created.json()["id"]

    terminated = agent_client.post(f"/api/agent/tasks/{task_id}/terminate")
    assert terminated.status_code == 200, terminated.text
    assert terminated.json()["status"] == "terminated"

    history = agent_client.get("/api/agent/history")
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["id"] == task_id
    assert history.json()["items"][0]["status"] == "terminated"
    assert history.json()["items"][0]["deletion_eligible"] is True

    deleted = agent_client.delete(f"/api/agent/tasks/{task_id}")
    assert deleted.status_code == 204, deleted.text


def test_rollback_preview_requires_a_separate_confirmation_before_locking(
    agent_client: TestClient,
) -> None:
    target_version_id = uuid4()

    async def seed_completed_task() -> str:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="completed",
                stage="terminal",
                workflow_version="new-agent-v1",
                task_kind="sync",
                title="已治理任务",
                idempotency_key=f"rollback-source-{uuid4()}",
                request_hash="e" * 64,
            )
            session.add(task)
            await session.flush()
            await AgentReportingService(session).generate(
                task_id=task.id,
                tenant_id=task.tenant_id,
                kind="sync",
                terminal_state="completed",
                facts={
                    "output_target_version_id": str(target_version_id),
                    "mutations": [
                        {
                            "id": str(uuid4()),
                            "status": "succeeded",
                            "verification": {"valid": True},
                            "operation": "update",
                            "entity_kind": "student",
                            "target_source_identifier": "csv:2",
                            "before": {"name": "旧姓名"},
                            "after": {"name": "新姓名"},
                        }
                    ],
                },
            )
            await session.commit()
            return str(task.id)

    rejected_source_task_id = agent_client.portal.call(seed_completed_task)
    rejected_preview = agent_client.post(
        f"/api/agent/tasks/{rejected_source_task_id}/rollback-preview"
    )
    rejected = agent_client.post(
        f"/api/agent/rollback-tasks/{rejected_preview.json()['task_id']}/reject"
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "terminated"
    assert rejected.json()["report_id"] is not None

    source_task_id = agent_client.portal.call(seed_completed_task)
    preview = agent_client.post(f"/api/agent/tasks/{source_task_id}/rollback-preview")
    assert preview.status_code == 201, preview.text
    assert preview.json()["requires_confirmation"] is True
    rollback_task_id = preview.json()["task_id"]

    task_before_confirmation = agent_client.get(f"/api/agent/tasks/{rollback_task_id}")
    assert task_before_confirmation.json()["phase"] == "intent_confirmed"
    assert task_before_confirmation.json()["status"] == "pending"

    confirmed = agent_client.post(f"/api/agent/rollback-tasks/{rollback_task_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["phase"] == "plan_restore"
    assert confirmed.json()["status"] == "running"

    terminated = agent_client.post(f"/api/agent/tasks/{rollback_task_id}/terminate")
    assert terminated.status_code == 200, terminated.text
    assert terminated.json()["status"] == "terminated"
    rollback_history = agent_client.get("/api/agent/history").json()["items"]
    rollback_item = next(item for item in rollback_history if item["id"] == rollback_task_id)
    assert rollback_item["task_kind"] == "rollback"
    assert rollback_item["parent_task_id"] == source_task_id


def test_completed_rollback_disables_same_csv_tasks_without_affecting_mysql(
    agent_client: TestClient,
) -> None:
    async def seed_cycles() -> tuple[str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            csv_target = {"kind": "csv", "upload_id": str(uuid4())}
            mysql_target = {
                "kind": "database",
                "configuration_id": "mysql-school-1",
            }
            csv_tasks = [
                ReconciliationTask(
                    tenant_id="school-1",
                    scope_id="all",
                    snapshot_mode="full",
                    entity_types=["student"],
                    status="completed",
                    stage="terminal",
                    workflow_version="new-agent-v1",
                    task_kind="sync",
                    title=f"CSV 同步 {index}",
                    agent_intent={"target": csv_target},
                    idempotency_key=f"csv-cycle-{index}-{uuid4()}",
                    request_hash="c" * 64,
                )
                for index in range(2)
            ]
            mysql_task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="completed",
                stage="terminal",
                workflow_version="new-agent-v1",
                task_kind="sync",
                title="MySQL 同步",
                agent_intent={"target": mysql_target},
                idempotency_key=f"mysql-cycle-{uuid4()}",
                request_hash="d" * 64,
            )
            session.add_all([*csv_tasks, mysql_task])
            await session.flush()
            reporting = AgentReportingService(session)
            for task in (*csv_tasks, mysql_task):
                await AgentRuntimeRepository(session).create_run(
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    conversation_id=None,
                    kind=AgentRunKind.SYNC,
                )
                await reporting.generate(
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    kind="sync",
                    terminal_state="completed",
                    facts={
                        "output_target_version_id": str(uuid4()),
                        "mutations": [
                            {
                                "id": str(uuid4()),
                                "status": "succeeded",
                                "verification": {"valid": True},
                            }
                        ],
                    },
                )
            rollback = await reporting.create_rollback_task(
                source_task_id=csv_tasks[-1].id,
                tenant_id="school-1",
                requested_by="operator-1",
                target_version_id=uuid4(),
            )
            await reporting.generate(
                task_id=rollback.task_id,
                tenant_id="school-1",
                kind="rollback",
                terminal_state="completed",
                facts={
                    "mutations": [
                        {
                            "id": str(uuid4()),
                            "status": "succeeded",
                            "verification": {"valid": True},
                        }
                    ]
                },
            )
            await session.commit()
            return str(csv_tasks[0].id), str(mysql_task.id)

    csv_task_id, mysql_task_id = agent_client.portal.call(seed_cycles)

    csv_task = agent_client.get(f"/api/agent/tasks/{csv_task_id}")
    assert csv_task.status_code == 200, csv_task.text
    assert csv_task.json()["rollback_eligible"] is False
    assert csv_task.json()["rollback_blocked_reason"] == "stale_sync_record"

    mysql_task = agent_client.get(f"/api/agent/tasks/{mysql_task_id}")
    assert mysql_task.status_code == 200, mysql_task.text
    assert mysql_task.json()["rollback_eligible"] is True
    assert mysql_task.json()["rollback_blocked_reason"] is None

    blocked_preview = agent_client.post(
        f"/api/agent/tasks/{csv_task_id}/rollback-preview"
    )
    assert blocked_preview.status_code == 409, blocked_preview.text
    assert blocked_preview.json()["detail"]["code"] == "rollback_sync_record_too_old"


def test_started_new_sync_marks_old_rollback_record_as_stale(
    agent_client: TestClient,
) -> None:
    async def seed_tasks() -> tuple[str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            target = {"kind": "csv", "upload_id": str(uuid4())}
            older = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="completed",
                stage="terminal",
                workflow_version="new-agent-v1",
                task_kind="sync",
                title="旧同步记录",
                agent_intent={"target": target},
                idempotency_key=f"stale-old-{uuid4()}",
                request_hash="a" * 64,
            )
            session.add(older)
            await session.flush()
            await AgentRuntimeRepository(session).create_run(
                task_id=older.id,
                tenant_id=older.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.SYNC,
            )
            await AgentReportingService(session).generate(
                task_id=older.id,
                tenant_id=older.tenant_id,
                kind="sync",
                terminal_state="completed",
                facts={
                    "output_target_version_id": str(uuid4()),
                    "mutations": [_verified_mutation_for_api("old-sync")],
                },
            )
            newer = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="created",
                stage="ingestion",
                workflow_version="new-agent-v1",
                task_kind="sync",
                title="新同步任务",
                agent_intent={"target": target},
                idempotency_key=f"stale-new-{uuid4()}",
                request_hash="b" * 64,
            )
            session.add(newer)
            await session.flush()
            await AgentRuntimeRepository(session).create_run(
                task_id=newer.id,
                tenant_id=newer.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.SYNC,
            )
            await AgentRollbackCycleService(session).record_sync_started(newer)
            await session.commit()
            return str(older.id), str(newer.id)

    def _verified_mutation_for_api(operation_id: str) -> dict[str, object]:
        return {
            "id": operation_id,
            "status": "succeeded",
            "verification": {"valid": True},
        }

    older_id, newer_id = agent_client.portal.call(seed_tasks)
    older = agent_client.get(f"/api/agent/tasks/{older_id}")
    assert older.status_code == 200, older.text
    assert older.json()["rollback_eligible"] is False
    assert older.json()["rollback_blocked_reason"] == "stale_sync_record"

    blocked_preview = agent_client.post(f"/api/agent/tasks/{older_id}/rollback-preview")
    assert blocked_preview.status_code == 409, blocked_preview.text
    assert blocked_preview.json()["detail"] == {
        "code": "rollback_sync_record_too_old",
        "message": "记录过旧，无法回滚",
    }

    newest = agent_client.get(f"/api/agent/tasks/{newer_id}")
    assert newest.status_code == 200, newest.text
    assert newest.json()["rollback_eligible"] is False
    assert newest.json()["rollback_blocked_reason"] is None


def test_agent_report_api_masks_student_phone_and_is_tenant_scoped(
    agent_client: TestClient,
) -> None:
    async def seed_report() -> tuple[str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="completed",
                stage="terminal",
                workflow_version="new-agent-v1",
                task_kind="sync",
                title="隐私报告",
                idempotency_key=f"private-report-{uuid4()}",
                request_hash="f" * 64,
            )
            session.add(task)
            await session.flush()
            report = await AgentReportingService(session).generate(
                task_id=task.id,
                tenant_id=task.tenant_id,
                kind="sync",
                terminal_state="completed",
                facts={
                    "mutations": [
                        {
                            "id": "op-private",
                            "status": "succeeded",
                            "before": {"phone": "13800138000"},
                            "after": {"phone": "13900139000"},
                            "verification": {"valid": True},
                        }
                    ]
                },
                narrative={"summary": "手机号 13800138000 已更新"},
            )
            await session.commit()
            return str(task.id), str(report.id)

    task_id, report_id = agent_client.portal.call(seed_report)
    response = agent_client.get(f"/api/agent/tasks/{task_id}/report")

    assert response.status_code == 200
    assert response.json()["id"] == report_id
    serialized = response.text
    assert "13800138000" not in serialized
    assert "13900139000" not in serialized
    assert "***8000" in serialized
    assert "***9000" in serialized

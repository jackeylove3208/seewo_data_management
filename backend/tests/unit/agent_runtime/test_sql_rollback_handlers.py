from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_runtime.csv_rollback_handlers import _rollback_operation
from app.agent_runtime.errors import ExternalWriteRecoveryRequired
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.sql_rollback_handlers import SqlRollbackExecutionHandler
from app.agent_runtime.worker import AgentWorkContext
from app.connectors.configured import (
    ConfiguredApiConnector,
    ConnectorCapabilities,
    ConnectorConflictError,
    DatabaseConnectorConfiguration,
)
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.repositories.executions import ExecutionRepository
from tests.fixtures.connector_store import InMemoryConnectorStore


class _Session:
    def __init__(self, task: object, target: object) -> None:
        self._task = task
        self._target = target

    async def get(self, model: type[object], _identifier: object) -> object:
        if model is ReconciliationTask:
            return self._task
        if model is TargetVersionRecord:
            return self._target
        raise AssertionError(f"unexpected model: {model}")


class _Resolver:
    def __init__(self, connector: ConfiguredApiConnector) -> None:
        self._connector = connector

    async def connector(self, connector_id: str) -> ConfiguredApiConnector:
        assert connector_id == "seewo-mysql"
        return self._connector


class _ConcurrentReadConnector(ConfiguredApiConnector):
    def __init__(self, connector: ConfiguredApiConnector) -> None:
        self.configuration = connector.configuration
        self._connector = connector
        self.armed = False
        self.drifted = False

    async def version(self):
        return await self._connector.version()

    async def read_record(self, identifier: str):
        snapshot = await self._connector.read_record(identifier)
        if self.armed and not self.drifted:
            self.drifted = True
            version = (await self._connector.version()).value
            await self._connector.apply(
                [
                    {
                        "operation": "update",
                        "id": identifier,
                        "before": {"remark": ""},
                        "after": {"remark": "operator changed this"},
                    }
                ],
                idempotency_key="concurrent-custom-field-change",
                expected_version=version,
            )
        return snapshot

    async def apply(self, *args, **kwargs):
        return await self._connector.apply(*args, **kwargs)

    async def verify(self, expected):
        return await self._connector.verify(expected)


def _connector(*, record_id: str = "student-1") -> ConfiguredApiConnector:
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-mysql",
        dialect="mysql",
        table_name="organization_people",
        primary_key="id",
        version_column="row_version",
        field_columns={
            "category": "category",
            "name": "name",
            "number": "number",
            "class_name": "class_name",
            "phone": "phone",
            "email": "email",
        },
        allowed_columns=(
            "id",
            "row_version",
            "category",
            "name",
            "number",
            "class_name",
            "phone",
            "email",
            "remark",
        ),
        source_role="target",
        capabilities=ConnectorCapabilities(
            read=True,
            paginated=True,
            create=True,
            update=True,
            delete=True,
            optimistic_version=True,
        ),
    )
    return ConfiguredApiConnector(
        configuration=configuration,
        store=InMemoryConnectorStore(
            records=[
                {
                    "id": record_id,
                    "row_version": "v1",
                    "category": "student",
                    "name": "张三",
                    "number": "S001",
                    "class_name": "一班",
                    "phone": "B",
                    "email": "student@example.test",
                    "remark": "",
                }
            ]
        ),
    )


def _rollback_facts():
    mutation = {
        "id": str(uuid4()),
        "operation": "update",
        "entity_kind": "student",
        "target_source_identifier": "database:seewo-mysql:student-1",
        "before": {"phone": "A"},
        "after": {"phone": "B"},
    }
    parent = SimpleNamespace(
        id=uuid4(),
        task_id=uuid4(),
        tenant_id="school-1",
        source_snapshot_id=uuid4(),
        file_sha256=sha256(b"v1").hexdigest(),
        storage_path="database://seewo-mysql/version/v1",
    )
    task = SimpleNamespace(
        id=uuid4(),
        parent_task_id=uuid4(),
        request_hash="request-hash",
        agent_intent={
            "source_task_id": str(uuid4()),
            "target_version_id": str(parent.id),
            "source_mode": "database",
            "target": {
                "kind": "database",
                "configuration_id": "seewo-mysql",
            },
            "operations": [mutation],
        },
    )
    context = AgentWorkContext(
        worker_id="sql-rollback-test-worker",
        run_id=uuid4(),
        task_id=task.id,
        tenant_id="school-1",
        phase="execute_restore",
        attempt_count=1,
        lease_token=uuid4(),
    )
    return mutation, parent, task, context


def _create_rollback_facts():
    mutation, parent, task, context = _rollback_facts()
    mutation.update(
        {
            "operation": "create",
            "before": None,
            "after": {
                "category": "student",
                "name": "张三",
                "number": "S001",
                "class_name": "一班",
                "phone": "B",
                "email": "student@example.test",
                "source_id": "student-1",
            },
        }
    )
    return mutation, parent, task, context


def _install_runtime_fakes(monkeypatch, parent):
    checkpoints: dict[str, object] = {}
    created_versions: list[dict[str, object]] = []

    async def get_checkpoint(_repository, *_args, **kwargs):
        payload = checkpoints.get(kwargs["checkpoint_key"])
        return SimpleNamespace(payload=payload) if payload is not None else None

    async def save_checkpoint(_repository, *_args, **kwargs):
        checkpoints[kwargs["checkpoint_key"]] = kwargs["payload"]
        return SimpleNamespace(payload=kwargs["payload"])

    async def current_target_version(_repository, _task_id):
        return parent

    async def create_target_version(_repository, **kwargs):
        created_versions.append(kwargs)
        return SimpleNamespace(
            id=uuid4(),
            storage_path=str(kwargs["storage_path"]),
        )

    monkeypatch.setattr(AgentRuntimeRepository, "get_checkpoint", get_checkpoint)
    monkeypatch.setattr(AgentRuntimeRepository, "save_checkpoint", save_checkpoint)
    monkeypatch.setattr(
        ExecutionRepository,
        "current_target_version",
        current_target_version,
    )
    monkeypatch.setattr(
        ExecutionRepository,
        "create_target_version",
        create_target_version,
    )
    return checkpoints, created_versions


@pytest.mark.asyncio
async def test_sql_rollback_uses_related_fields_after_unrelated_version_change(
    monkeypatch,
) -> None:
    connector = _connector()
    mutation, parent, task, context = _rollback_facts()
    checkpoints, created_versions = _install_runtime_fakes(
        monkeypatch,
        parent,
    )
    handler = SqlRollbackExecutionHandler(_Resolver(connector))
    session = _Session(task, parent)

    await handler.plan(session, context)  # type: ignore[arg-type]
    planned = task.agent_intent["restore_comparisons"][0]
    assert planned["disposition"] == "safe_to_restore"

    external_version = (await connector.version()).value
    await connector.apply(
        [
            {
                "operation": "update",
                "id": "student-1",
                "before": {"class_name": "一班"},
                "after": {"class_name": "二班"},
            }
        ],
        idempotency_key="unrelated-external-change",
        expected_version=external_version,
    )

    operation = _rollback_operation(
        mutation,
        target_version=f"sha256:{parent.file_sha256}",
    )
    fact = await handler.execute_operation(
        session,  # type: ignore[arg-type]
        context,
        operation.id,
    )

    current = await connector.read_record("student-1")
    assert fact["status"] == "succeeded"
    assert current is not None
    assert current["phone"] == "A"
    assert current["class_name"] == "二班"
    assert created_versions
    assert any(
        key.startswith("agent-sql-rollback-operation:")
        for key in checkpoints
    )


@pytest.mark.asyncio
async def test_sql_rollback_skips_related_drift_without_writing(
    monkeypatch,
) -> None:
    connector = _connector()
    mutation, parent, task, context = _rollback_facts()
    _checkpoints, created_versions = _install_runtime_fakes(
        monkeypatch,
        parent,
    )
    handler = SqlRollbackExecutionHandler(_Resolver(connector))
    session = _Session(task, parent)

    await handler.plan(session, context)  # type: ignore[arg-type]
    external_version = (await connector.version()).value
    await connector.apply(
        [
            {
                "operation": "update",
                "id": "student-1",
                "before": {"phone": "B"},
                "after": {"phone": "C"},
            }
        ],
        idempotency_key="related-external-change",
        expected_version=external_version,
    )
    operation = _rollback_operation(
        mutation,
        target_version=f"sha256:{parent.file_sha256}",
    )

    fact = await handler.execute_operation(
        session,  # type: ignore[arg-type]
        context,
        operation.id,
    )

    current = await connector.read_record("student-1")
    assert fact["status"] == "conflict_skipped"
    assert fact["safe_error_code"] == "rollback_current_data_conflict"
    assert current is not None and current["phone"] == "C"
    assert created_versions == []


@pytest.mark.asyncio
async def test_sql_rollback_replays_after_external_write_fact_persistence_fails(
    monkeypatch,
) -> None:
    connector = _connector()
    mutation, parent, task, context = _rollback_facts()
    _checkpoints, _created_versions = _install_runtime_fakes(
        monkeypatch,
        parent,
    )
    handler = SqlRollbackExecutionHandler(_Resolver(connector))
    session = _Session(task, parent)
    await handler.plan(session, context)  # type: ignore[arg-type]
    operation = _rollback_operation(
        mutation,
        target_version=f"sha256:{parent.file_sha256}",
    )

    async def fail_target_version(_repository, **_kwargs):
        raise ValueError("local target version persistence failed")

    monkeypatch.setattr(
        ExecutionRepository,
        "create_target_version",
        fail_target_version,
    )
    with pytest.raises(ExternalWriteRecoveryRequired):
        await handler.execute_operation(
            session,  # type: ignore[arg-type]
            context,
            operation.id,
        )
    applied = await connector.read_record("student-1")
    assert applied is not None and applied["phone"] == "A"

    async def recover_target_version(_repository, **kwargs):
        return SimpleNamespace(
            id=uuid4(),
            storage_path=str(kwargs["storage_path"]),
        )

    monkeypatch.setattr(
        ExecutionRepository,
        "create_target_version",
        recover_target_version,
    )
    recovered = await handler.execute_operation(
        session,  # type: ignore[arg-type]
        context,
        operation.id,
    )

    assert recovered["status"] == "already_restored"
    assert recovered["verification"]["idempotent_recovery"] is True


@pytest.mark.asyncio
async def test_sql_rollback_deletes_create_using_persisted_generated_locator(
    monkeypatch,
) -> None:
    connector = _connector(record_id="41")
    mutation, parent, task, context = _create_rollback_facts()
    mutation["target_source_identifier"] = "database:seewo-mysql:41"
    _checkpoints, _created_versions = _install_runtime_fakes(monkeypatch, parent)
    handler = SqlRollbackExecutionHandler(_Resolver(connector))
    session = _Session(task, parent)

    await handler.plan(session, context)  # type: ignore[arg-type]
    operation = _rollback_operation(
        mutation,
        target_version=f"sha256:{parent.file_sha256}",
    )
    fact = await handler.execute_operation(
        session,  # type: ignore[arg-type]
        context,
        operation.id,
    )

    assert fact["status"] == "succeeded"
    assert fact["target_source_identifier"] == "database:seewo-mysql:41"
    assert await connector.read_record("41") is None


@pytest.mark.asyncio
async def test_sql_rollback_rejects_custom_field_drift_between_read_and_delete(
    monkeypatch,
) -> None:
    connector = _ConcurrentReadConnector(_connector())
    mutation, parent, task, context = _create_rollback_facts()
    _checkpoints, created_versions = _install_runtime_fakes(
        monkeypatch,
        parent,
    )
    handler = SqlRollbackExecutionHandler(_Resolver(connector))
    session = _Session(task, parent)

    await handler.plan(session, context)  # type: ignore[arg-type]
    operation = _rollback_operation(
        mutation,
        target_version=f"sha256:{parent.file_sha256}",
    )
    connector.armed = True

    with pytest.raises(ConnectorConflictError):
        await handler.execute_operation(
            session,  # type: ignore[arg-type]
            context,
            operation.id,
        )

    current = await connector.read_record("student-1")
    assert current is not None
    assert current["remark"] == "operator changed this"
    assert created_versions == []

    retried = await handler.execute_operation(
        session,  # type: ignore[arg-type]
        context,
        operation.id,
    )
    current_after_retry = await connector.read_record("student-1")
    assert retried["status"] == "conflict_skipped"
    assert current_after_retry is not None
    assert current_after_retry["remark"] == "operator changed this"

from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.agent_graph.contracts import AllowedActionV1
from app.agent_graph.production_executor import ProductionGraphActionExecutor
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.runtime import ProductionGraphCandidateProvider
from app.agent_graph.worker import GraphWorkContext
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.task_service import (
    AgentConnectorCapabilityFailure,
    AgentTaskService,
)
from app.api_connectors.contracts import (
    ApiProviderError,
    CapturedApiPage,
    ConnectionTestResult,
    FrozenApiRecord,
    ProviderManifest,
)
from app.api_connectors.materializer import ApiAuthorityMaterializer, ApiSourceFailure
from app.api_connectors.registry import ProviderRegistry
from app.api_connectors.secrets import EncryptedDatabaseSecretStore
from app.connectors.configured import ConfiguredApiConnector
from app.core.security import OperatorContext
from app.models.agent_analysis import (
    AgentInputMarkRecord,
    AgentInputRecord,
    AgentWorkItemRecord,
)
from app.models.api_connectors import (
    AgentSourceBindingRecord,
    ApiAuthoritySourceRecord,
    ApiConnectionRecord,
)
from app.models.mappings import EntityMapping
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import (
    CanonicalEntityRecord,
    RawSnapshotRow,
    Snapshot,
    SourceFile,
)
from app.reconciliation.agent_identity import AgentIdentityIndexBuilder
from app.schemas.agent_api import AgentTaskIntent
from app.schemas.agent_ingestion import AgentEntityKind
from tests.fixtures.connector_store import InMemoryConnectorStore
from tests.settings import build_test_settings

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


class AdapterMustNotRun:
    manifest = MANIFEST

    def __init__(self) -> None:
        self.calls = 0

    async def test_connection(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
    ) -> ConnectionTestResult:
        del public_configuration, secret
        self.calls += 1
        raise AssertionError("task binding must not call provider")

    async def capture(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        selected_entities: frozenset[AgentEntityKind],
    ) -> AsyncIterator[CapturedApiPage]:
        del public_configuration, secret, selected_entities
        self.calls += 1
        raise AssertionError("task binding must not call provider")
        yield

class CaptureAdapter(AdapterMustNotRun):
    async def capture(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        selected_entities: frozenset[AgentEntityKind],
    ) -> AsyncIterator[CapturedApiPage]:
        assert public_configuration == {"person_entity_kind": "teacher"}
        assert secret == {"client_id": "client", "client_secret": "secret"}
        assert selected_entities == frozenset({AgentEntityKind.TEACHER})
        self.calls += 1
        yield CapturedApiPage(
            page_number=1,
            records=(
                FrozenApiRecord(
                    external_id="teacher-1",
                    entity_kind=AgentEntityKind.TEACHER,
                    provider_fields={"userid": "teacher-1", "name": "周明远"},
                    projected_fields={
                        "category": "教师",
                        "name": "周明远",
                        "number": None,
                        "class_name": None,
                        "phone": "138 0000 0001",
                        "email": None,
                    },
                    unavailable_fields=("email", "number"),
                ),
            ),
            next_cursor=None,
        )


class FailingCaptureAdapter(AdapterMustNotRun):
    async def capture(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        selected_entities: frozenset[AgentEntityKind],
    ) -> AsyncIterator[CapturedApiPage]:
        del public_configuration, secret, selected_entities
        self.calls += 1
        raise ApiProviderError("connector_timeout")
        yield


class ModelMustNotRun:
    async def complete_json_once(self, _request):
        raise AssertionError("API materialization must not call a model")


class StaticDatabaseConnectorRuntime:
    def __init__(self, connector: ConfiguredApiConnector) -> None:
        self.connector_instance = connector

    async def connector(self, connector_id: str) -> ConfiguredApiConnector:
        assert connector_id == "seewo-mysql"
        return self.connector_instance


def _action(
    action_id: str,
    graph_action_kind: str,
    resource_id: str,
    evidence: str,
) -> AllowedActionV1:
    return AllowedActionV1(
        action_id=action_id,
        graph_action_kind=graph_action_kind,
        kind="run_deterministic",
        resource_ids=(resource_id,),
        required_evidence=(evidence,),
        risk="low",
        requires_human=False,
        successor_node="inspect_sources",
    )


def _settings(fernet_key: bytes):
    return build_test_settings(
        new_agent_enabled=True,
        agent_graph_enabled=True,
        source_ingestion_v3_enabled=True,
        agent_graph_sql_execution_enabled=True,
        new_agent_analysis_only=False,
        new_agent_api_connector_enabled=True,
        api_connector_secret_key=fernet_key.decode(),
        database_connector_configurations={
            "seewo-mysql": {
                "credential_reference": "secret://connectors/seewo-mysql",
                "dialect": "mysql",
                "table_name": "organization_people",
                "primary_key": "id",
                "version_column": "row_version",
                "field_columns": {
                    "category": "category",
                    "name": "name",
                    "number": "number",
                    "class_name": "class_name",
                    "phone": "phone",
                    "email": "email",
                },
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
            "secret://connectors/seewo-mysql": "mysql+asyncmy://hidden"
        },
    )


async def _seed_connection(
    session,
    *,
    fernet_key: bytes,
    tenant_id: str = "school-1",
    scope: str = "persistent",
    conversation_id=None,
    manifest: ProviderManifest = MANIFEST,
) -> ApiConnectionRecord:
    secret_ref = await EncryptedDatabaseSecretStore(
        session,
        fernet_key=fernet_key,
    ).put(
        tenant_id=tenant_id,
        payload={"client_id": "client", "client_secret": "secret"},
    )
    connection = ApiConnectionRecord(
        tenant_id=tenant_id,
        provider_id=manifest.provider_id,
        display_name="权威通讯录",
        scope=scope,
        conversation_id=conversation_id,
        public_configuration={"person_entity_kind": "teacher"},
        secret_ref=secret_ref,
        manifest_version=manifest.manifest_version,
        adapter_version=manifest.adapter_version,
        capabilities={"entity.teacher.read": True},
        visibility_summary={"visible": True, "teacher_count": 2},
        state="active",
        last_tested_at=datetime.now(UTC),
        created_by="operator-1",
        updated_by="operator-1",
    )
    session.add(connection)
    await session.flush()
    return connection


def _intent(connection_id) -> AgentTaskIntent:
    return AgentTaskIntent.model_validate(
        {
            "title": "同步教师通讯录到希沃模拟库",
            "entity_types": ["teacher"],
            "source": {"kind": "api", "configuration_id": str(connection_id)},
            "target": {"kind": "database", "configuration_id": "seewo-mysql"},
        }
    )


async def test_api_task_binds_resource_and_selects_graph_v2_without_provider_call(
    database,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key)
    adapter = AdapterMustNotRun()
    registry = ProviderRegistry()
    registry.register(MANIFEST, adapter)
    async with database.session_factory() as session:
        async with session.begin():
            connection = await _seed_connection(session, fernet_key=key)
            task, run = await AgentTaskService(
                session,
                operator=OperatorContext(
                    operator_id="operator-1",
                    tenant_id="school-1",
                ),
                settings=settings,
                provider_registry=registry,
            ).create(
                _intent(connection.id),
                idempotency_key="api-task-1",
            )
            graph = await AgentGraphRepository(session).get_run_state_for_agent_run(
                run.id
            )
            assert graph is not None
            api_source = await session.scalar(
                select(ApiAuthoritySourceRecord).where(
                    ApiAuthoritySourceRecord.task_id == task.id
                )
            )
            sources = tuple(
                await session.scalars(
                    select(SourceFile).where(SourceFile.task_id == task.id)
                )
            )
            snapshots = tuple(
                await session.scalars(
                    select(Snapshot).where(Snapshot.task_id == task.id)
                )
            )
            role_bindings = tuple(
                await session.scalars(
                    select(AgentSourceBindingRecord)
                    .where(AgentSourceBindingRecord.task_id == task.id)
                    .order_by(AgentSourceBindingRecord.role)
                )
            )

    assert run.ingestion_contract_version == "source-ingestion-v3"
    assert graph.graph_version == "agent-sync-graph-v2"
    assert graph.current_node == "materialize_sources"
    assert api_source is not None
    assert api_source.connection_id == connection.id
    assert api_source.selected_entities == ["teacher"]
    assert [source.source_role for source in sources] == ["target"]
    assert sources[0].storage_path == "database://seewo-mysql"
    assert [snapshot.source_role for snapshot in snapshots] == ["target"]
    assert [binding.role for binding in role_bindings] == [
        "authoritative",
        "target",
    ]
    target_binding = role_bindings[1]
    assert target_binding.configuration_id == "seewo-mysql"
    assert target_binding.snapshot_id == snapshots[0].id
    assert target_binding.frozen_public_configuration["table_name"] == (
        settings.database_connector_configurations["seewo-mysql"].table_name
    )
    assert target_binding.credential_reference == (
        settings.database_connector_configurations["seewo-mysql"].credential_reference
    )
    assert len(target_binding.configuration_fingerprint) == 64
    assert adapter.calls == 0

    context = GraphWorkContext(
        worker_id="api-materialization-worker",
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        graph_run_id=graph.id,
        graph_version=graph.graph_version,
        current_node=graph.current_node,
        graph_cursor=graph.cursor,
        attempt_count=run.attempt_count,
        lease_token=uuid4(),
        ingestion_contract_version=run.ingestion_contract_version,
        execution_contract_version=run.execution_contract_version,
    )
    plan = await ProductionGraphCandidateProvider(database.session_factory)(context)
    actions = [item.action for item in plan.candidate_evaluations if item.passed]
    assert len(actions) == 1
    assert actions[0].graph_action_kind == "materialize_remote_authority"
    assert actions[0].resource_ids == (f"api-source:{api_source.id}",)


async def test_api_task_freezes_dingtalk_scope_classification_without_model_call(
    database,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key)
    adapter = AdapterMustNotRun()
    registry = ProviderRegistry()
    registry.register(MANIFEST, adapter)
    classification = {
        "mode": "organization_unit_llm",
        "skill_version": "1.0.0",
        "tree_fingerprint": "a" * 64,
        "input_hash": "b" * 64,
        "output_hash": "c" * 64,
        "attempts": [],
    }
    async with database.session_factory() as session:
        async with session.begin():
            connection = await _seed_connection(session, fernet_key=key)
            connection.public_configuration = {
                "sync_scope": "people",
                "root_department_id": 1,
                "person_classification_mode": "organization_unit_llm",
                "department_entity_kinds": {
                    "10": "teacher",
                    "11": "teacher",
                    "20": "student",
                    "21": "student",
                },
                "organization_classification": classification,
            }
            connection.capabilities = {
                "entity.teacher.read": True,
                "entity.student.read": True,
            }
            connection.visibility_summary = {
                "visible": True,
                "teacher_count": 2,
                "student_count": 2,
            }
            intent = AgentTaskIntent.model_validate(
                {
                    "title": "钉钉人员同步",
                    "entity_types": ["teacher", "student"],
                    "source": {
                        "kind": "api",
                        "configuration_id": str(connection.id),
                    },
                    "target": {
                        "kind": "database",
                        "configuration_id": "seewo-mysql",
                    },
                }
            )
            task, _run = await AgentTaskService(
                session,
                operator=OperatorContext(
                    operator_id="operator-1",
                    tenant_id="school-1",
                ),
                settings=settings,
                provider_registry=registry,
            ).create(intent, idempotency_key="api-task-frozen-classification")
            api_source = await session.scalar(
                select(ApiAuthoritySourceRecord).where(
                    ApiAuthoritySourceRecord.task_id == task.id
                )
            )

    assert api_source is not None
    assert api_source.selected_entities == ["student", "teacher"]
    assert api_source.frozen_public_configuration[
        "department_entity_kinds"
    ] == {
        "10": "teacher",
        "11": "teacher",
        "20": "student",
        "21": "student",
    }
    assert api_source.frozen_public_configuration[
        "organization_classification"
    ] == classification
    assert adapter.calls == 0


async def test_conversation_api_task_rejects_a_persistent_connection(
    database,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key)
    dingtalk_manifest = MANIFEST.model_copy(update={"provider_id": "dingtalk"})
    adapter = AdapterMustNotRun()
    adapter.manifest = dingtalk_manifest
    registry = ProviderRegistry()
    registry.register(dingtalk_manifest, adapter)
    async with database.session_factory() as session:
        async with session.begin():
            conversation = await AgentRuntimeRepository(session).create_conversation(
                tenant_id="school-1",
                created_by="operator-1",
            )
            connection = await _seed_connection(
                session,
                fernet_key=key,
                manifest=dingtalk_manifest,
            )

            with pytest.raises(
                AgentConnectorCapabilityFailure,
                match="task-ephemeral",
            ):
                await AgentTaskService(
                    session,
                    operator=OperatorContext(
                        operator_id="operator-1",
                        tenant_id="school-1",
                    ),
                    settings=settings,
                    provider_registry=registry,
                ).create(
                    _intent(connection.id),
                    idempotency_key="persistent-conversation-api-task",
                    conversation_id=conversation.id,
                )


async def test_non_dingtalk_conversation_task_keeps_persistent_connection_compatible(
    database,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key)
    registry = ProviderRegistry()
    registry.register(MANIFEST, AdapterMustNotRun())
    async with database.session_factory() as session:
        async with session.begin():
            conversation = await AgentRuntimeRepository(session).create_conversation(
                tenant_id="school-1",
                created_by="operator-1",
            )
            connection = await _seed_connection(session, fernet_key=key)

            task, _run = await AgentTaskService(
                session,
                operator=OperatorContext(
                    operator_id="operator-1",
                    tenant_id="school-1",
                ),
                settings=settings,
                provider_registry=registry,
            ).create(
                _intent(connection.id),
                idempotency_key="persistent-non-dingtalk-conversation-task",
                conversation_id=conversation.id,
            )

            assert task.id is not None
            assert connection.task_id is None


async def test_conversation_api_task_atomically_binds_its_ephemeral_connection(
    database,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key)
    registry = ProviderRegistry()
    registry.register(MANIFEST, AdapterMustNotRun())
    async with database.session_factory() as session:
        async with session.begin():
            conversation = await AgentRuntimeRepository(session).create_conversation(
                tenant_id="school-1",
                created_by="operator-1",
            )
            connection = await _seed_connection(
                session,
                fernet_key=key,
                scope="task_ephemeral",
                conversation_id=conversation.id,
            )

            service = AgentTaskService(
                session,
                operator=OperatorContext(
                    operator_id="operator-1",
                    tenant_id="school-1",
                ),
                settings=settings,
                provider_registry=registry,
            )
            task, _run = await service.create(
                _intent(connection.id),
                idempotency_key="ephemeral-conversation-api-task",
                conversation_id=conversation.id,
            )
            replayed_task, _replayed_run = await service.create(
                _intent(connection.id),
                idempotency_key="ephemeral-conversation-api-task",
                conversation_id=conversation.id,
            )

            assert connection.task_id == task.id
            assert connection.consumed_task_id == task.id
            assert replayed_task.id == task.id


async def test_conversation_api_task_rejects_expired_dingtalk_credentials(
    database,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key)
    dingtalk_manifest = MANIFEST.model_copy(update={"provider_id": "dingtalk"})
    adapter = AdapterMustNotRun()
    adapter.manifest = dingtalk_manifest
    registry = ProviderRegistry()
    registry.register(dingtalk_manifest, adapter)
    async with database.session_factory() as session:
        async with session.begin():
            conversation = await AgentRuntimeRepository(session).create_conversation(
                tenant_id="school-1",
                created_by="operator-1",
            )
            connection = await _seed_connection(
                session,
                fernet_key=key,
                scope="task_ephemeral",
                conversation_id=conversation.id,
                manifest=dingtalk_manifest,
            )
            connection.created_at = datetime.now(UTC) - timedelta(hours=25)

            with pytest.raises(
                AgentConnectorCapabilityFailure,
                match="expired",
            ):
                await AgentTaskService(
                    session,
                    operator=OperatorContext(
                        operator_id="operator-1",
                        tenant_id="school-1",
                    ),
                    settings=settings,
                    provider_registry=registry,
                ).create(
                    _intent(connection.id),
                    idempotency_key="expired-dingtalk-conversation-task",
                    conversation_id=conversation.id,
                )


async def test_api_task_rejects_cross_tenant_connection_before_creating_task(
    database,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key)
    adapter = AdapterMustNotRun()
    registry = ProviderRegistry()
    registry.register(MANIFEST, adapter)
    async with database.session_factory() as session:
        async with session.begin():
            connection = await _seed_connection(
                session,
                fernet_key=key,
                tenant_id="school-2",
            )
            with pytest.raises(
                AgentConnectorCapabilityFailure,
                match="connection",
            ):
                await AgentTaskService(
                    session,
                    operator=OperatorContext(
                        operator_id="operator-1",
                        tenant_id="school-1",
                    ),
                    settings=settings,
                    provider_registry=registry,
                ).create(
                    _intent(connection.id),
                    idempotency_key="api-task-cross-tenant",
                )
            assert await session.scalar(select(ReconciliationTask)) is None
    assert adapter.calls == 0


async def test_api_task_rejects_a_stale_connection_test_before_task_creation(
    database,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key)
    registry = ProviderRegistry()
    registry.register(MANIFEST, AdapterMustNotRun())
    async with database.session_factory() as session:
        async with session.begin():
            connection = await _seed_connection(session, fernet_key=key)
            connection.last_tested_at = datetime.now(UTC) - timedelta(days=2)

            with pytest.raises(
                AgentConnectorCapabilityFailure,
                match="tested again",
            ):
                await AgentTaskService(
                    session,
                    operator=OperatorContext(
                        operator_id="operator-1",
                        tenant_id="school-1",
                    ),
                    settings=settings,
                    provider_registry=registry,
                ).create(
                    _intent(connection.id),
                    idempotency_key="api-task-stale-connection",
                )


async def test_graph_api_materialization_action_publishes_task_bound_authority(
    database,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key).model_copy(
        update={"upload_root": tmp_path / "uploads"}
    )
    adapter = CaptureAdapter()
    registry = ProviderRegistry()
    registry.register(MANIFEST, adapter)
    async with database.session_factory() as session:
        async with session.begin():
            connection = await _seed_connection(session, fernet_key=key)
            task, run = await AgentTaskService(
                session,
                operator=OperatorContext(
                    operator_id="operator-1",
                    tenant_id="school-1",
                ),
                settings=settings,
                provider_registry=registry,
            ).create(
                _intent(connection.id),
                idempotency_key="api-task-materialization",
            )
            graph = await AgentGraphRepository(session).get_run_state_for_agent_run(
                run.id
            )
            assert graph is not None
            api_source = await session.scalar(
                select(ApiAuthoritySourceRecord).where(
                    ApiAuthoritySourceRecord.task_id == task.id
                )
            )
            assert api_source is not None
            assert api_source.frozen_public_configuration == {
                "person_entity_kind": "teacher"
            }
            frozen_secret_ref = api_source.frozen_secret_ref

    async with database.session_factory() as session:
        async with session.begin():
            connection = await session.get(ApiConnectionRecord, connection.id)
            assert connection is not None
            await EncryptedDatabaseSecretStore(
                session,
                fernet_key=key,
            ).rotate(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                payload={
                    "client_id": "replacement-client",
                    "client_secret": "replacement-secret",
                },
            )
            connection.public_configuration = {"person_entity_kind": "student"}
            connection.state = "pending"
            assert connection.secret_ref != frozen_secret_ref

    context = GraphWorkContext(
        worker_id="api-materialization-worker",
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        graph_run_id=graph.id,
        graph_version=graph.graph_version,
        current_node=graph.current_node,
        graph_cursor=graph.cursor,
        attempt_count=run.attempt_count,
        lease_token=uuid4(),
        ingestion_contract_version=run.ingestion_contract_version,
        execution_contract_version=run.execution_contract_version,
    )
    plan = await ProductionGraphCandidateProvider(database.session_factory)(context)
    action = next(
        item.action for item in plan.candidate_evaluations if item.passed
    )
    executor = ProductionGraphActionExecutor(
        database.session_factory,
        provider=ModelMustNotRun(),
        tokenization_secret="test-tokenization-secret",
        settings=settings,
        api_materializer=ApiAuthorityMaterializer(
            settings,
            registry=registry,
            fernet_key=key,
        ),
    )

    outcome = await executor(context, action)

    assert outcome.evidence_refs == (f"api-source:{api_source.id}:materialized",)
    assert adapter.calls == 1
    async with database.session_factory() as session:
        persisted = await session.get(ApiAuthoritySourceRecord, api_source.id)
        authority = await session.scalar(
            select(SourceFile).where(
                SourceFile.task_id == task.id,
                SourceFile.source_role == "authoritative",
            )
        )
        assert persisted is not None and persisted.state == "ready"
        assert authority is not None
        assert persisted.source_file_id == authority.id


async def test_graph_api_materialization_failure_persists_only_safe_code(
    database,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key).model_copy(
        update={"upload_root": tmp_path / "uploads"}
    )
    adapter = FailingCaptureAdapter()
    registry = ProviderRegistry()
    registry.register(MANIFEST, adapter)
    async with database.session_factory() as session:
        async with session.begin():
            connection = await _seed_connection(session, fernet_key=key)
            task, run = await AgentTaskService(
                session,
                operator=OperatorContext(
                    operator_id="operator-1",
                    tenant_id="school-1",
                ),
                settings=settings,
                provider_registry=registry,
            ).create(
                _intent(connection.id),
                idempotency_key="api-task-materialization-failure",
            )
            graph = await AgentGraphRepository(session).get_run_state_for_agent_run(
                run.id
            )
            api_source = await session.scalar(
                select(ApiAuthoritySourceRecord).where(
                    ApiAuthoritySourceRecord.task_id == task.id
                )
            )
            assert graph is not None and api_source is not None

    context = GraphWorkContext(
        worker_id="api-materialization-worker",
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        graph_run_id=graph.id,
        graph_version=graph.graph_version,
        current_node=graph.current_node,
        graph_cursor=graph.cursor,
        attempt_count=run.attempt_count,
        lease_token=uuid4(),
        ingestion_contract_version=run.ingestion_contract_version,
        execution_contract_version=run.execution_contract_version,
    )
    plan = await ProductionGraphCandidateProvider(database.session_factory)(context)
    action = next(
        item.action for item in plan.candidate_evaluations if item.passed
    )
    executor = ProductionGraphActionExecutor(
        database.session_factory,
        provider=ModelMustNotRun(),
        tokenization_secret="test-tokenization-secret",
        settings=settings,
        api_materializer=ApiAuthorityMaterializer(
            settings,
            registry=registry,
            fernet_key=key,
        ),
    )

    with pytest.raises(ApiSourceFailure, match="connector_timeout"):
        await executor(context, action)

    async with database.session_factory() as session:
        persisted = await session.get(ApiAuthoritySourceRecord, api_source.id)
        authority = await session.scalar(
            select(SourceFile).where(
                SourceFile.task_id == task.id,
                SourceFile.source_role == "authoritative",
            )
        )
        assert persisted is not None and persisted.state == "failed"
        assert persisted.safe_problem_code == "connector_timeout"
        assert authority is None


async def test_ingestion_v3_routes_api_authority_and_database_target_to_agent_inputs(
    database,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key).model_copy(
        update={"upload_root": tmp_path / "uploads"}
    )
    adapter = CaptureAdapter()
    registry = ProviderRegistry()
    registry.register(MANIFEST, adapter)
    async with database.session_factory() as session:
        async with session.begin():
            connection = await _seed_connection(session, fernet_key=key)
            task, run = await AgentTaskService(
                session,
                operator=OperatorContext(
                    operator_id="operator-1",
                    tenant_id="school-1",
                ),
                settings=settings,
                provider_registry=registry,
            ).create(
                _intent(connection.id),
                idempotency_key="api-task-ingestion-v3",
            )
            graph = await AgentGraphRepository(session).get_run_state_for_agent_run(
                run.id
            )
            api_source = await session.scalar(
                select(ApiAuthoritySourceRecord).where(
                    ApiAuthoritySourceRecord.task_id == task.id
                )
            )
            assert graph is not None and api_source is not None
    materializer = ApiAuthorityMaterializer(
        settings,
        registry=registry,
        fernet_key=key,
    )
    async with database.session_factory() as session:
        async with session.begin():
            await materializer.materialize(
                session,
                task_id=task.id,
                api_source_id=api_source.id,
            )

    target_connector = ConfiguredApiConnector(
        configuration=settings.database_connector_configurations["seewo-mysql"],
        store=InMemoryConnectorStore(
            records=[
                {
                    "id": "target-teacher-1",
                    "row_version": "v1",
                    "category": "教师",
                    "name": "周明远",
                    "number": None,
                    "class_name": None,
                    "phone": "13800000001",
                    "email": "target@example.test",
                }
            ]
        ),
    )
    database_connectors = StaticDatabaseConnectorRuntime(target_connector)
    executor = ProductionGraphActionExecutor(
        database.session_factory,
        provider=ModelMustNotRun(),
        tokenization_secret="test-tokenization-secret",
        settings=settings,
        database_connectors=database_connectors,
    )
    context = GraphWorkContext(
        worker_id="api-ingestion-v3-worker",
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        graph_run_id=graph.id,
        graph_version=graph.graph_version,
        current_node="inspect_sources",
        graph_cursor=graph.cursor,
        attempt_count=run.attempt_count,
        lease_token=uuid4(),
        ingestion_contract_version=run.ingestion_contract_version,
        execution_contract_version=run.execution_contract_version,
    )
    plan = await ProductionGraphCandidateProvider(
        database.session_factory,
        database_connectors=database_connectors,
    )(context)
    authority_inspection = next(
        item.action for item in plan.candidate_evaluations if item.passed
    )
    assert authority_inspection.kind == "run_deterministic"
    assert authority_inspection.resource_ids == ("source:authoritative:full",)
    await executor(context, authority_inspection)
    await executor(
        context,
        _action(
            "inspect_target:source",
            "inspect_target",
            "source:target:full",
            "source:target:inspection",
        ),
    )

    normalization_context = replace(
        context,
        current_node="normalize_input_batches",
    )
    expected_resources = (
        "source:authoritative:mapping",
        "source:target:mapping",
        "source:authoritative:full",
        "source:target:full",
    )
    for expected_resource in expected_resources:
        plan = await ProductionGraphCandidateProvider(
            database.session_factory,
            database_connectors=database_connectors,
        )(normalization_context)
        action = next(
            item.action for item in plan.candidate_evaluations if item.passed
        )
        assert action.resource_ids == (expected_resource,)
        await executor(normalization_context, action)

    validation_plan = await ProductionGraphCandidateProvider(
        database.session_factory,
        database_connectors=database_connectors,
    )(
        replace(
            context,
            current_node="validate_input_contract",
        )
    )
    validation_action = next(
        item.action
        for item in validation_plan.candidate_evaluations
        if item.passed
    )
    assert validation_action.graph_action_kind == "build_identity_index"

    async with database.session_factory() as session:
        async with session.begin():
            await AgentIdentityIndexBuilder(session).build(run_id=run.id)
        inputs = tuple(
            await session.scalars(
                select(AgentInputRecord)
                .where(AgentInputRecord.run_id == run.id)
                .order_by(AgentInputRecord.source_role)
            )
        )
        marks = tuple(await session.scalars(select(AgentInputMarkRecord)))
        work_items = tuple(
            await session.scalars(
                select(AgentWorkItemRecord).where(
                    AgentWorkItemRecord.run_id == run.id
                )
            )
        )
        assert [item.source_role for item in inputs] == [
            "authoritative",
            "target",
        ]
        authority = inputs[0]
        assert authority.stable_locator == (
            f"api:{connection.id}:teacher:teacher-1"
        )
        assert authority.number is None
        assert marks[0].reason_code == "authority_field_unavailable"
        assert marks[0].inclusion_state == "included"
        assert [item.kind for item in work_items] == ["correct"]
        assert (
            await session.scalar(select(func.count()).select_from(RawSnapshotRow))
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(CanonicalEntityRecord)
            )
            == 0
        )
        assert (
            await session.scalar(select(func.count()).select_from(EntityMapping))
            == 0
        )

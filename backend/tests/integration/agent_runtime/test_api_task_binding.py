from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.agent_graph.production_executor import ProductionGraphActionExecutor
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.runtime import ProductionGraphCandidateProvider
from app.agent_graph.worker import GraphWorkContext
from app.agent_runtime.task_service import (
    AgentConnectorCapabilityFailure,
    AgentTaskService,
)
from app.api_connectors.contracts import (
    AgentProjectionContext,
    ApiProviderError,
    CapturedApiPage,
    ConnectionTestResult,
    FrozenApiRecord,
    ProviderManifest,
)
from app.api_connectors.materializer import ApiAuthorityMaterializer, ApiSourceFailure
from app.api_connectors.registry import ProviderRegistry
from app.api_connectors.secrets import EncryptedDatabaseSecretStore
from app.core.security import OperatorContext
from app.models.api_connectors import ApiAuthoritySourceRecord, ApiConnectionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.schemas.agent_api import AgentTaskIntent
from app.schemas.agent_ingestion import AgentContractRecord, AgentEntityKind
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

    def project(
        self,
        record: FrozenApiRecord,
        context: AgentProjectionContext,
    ) -> AgentContractRecord:
        del record, context
        self.calls += 1
        raise AssertionError("task binding must not call provider")


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
                        "phone": None,
                        "email": None,
                    },
                    unavailable_fields=("email", "number", "phone"),
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

    assert run.ingestion_contract_version == "source-ingestion-v3"
    assert graph.graph_version == "agent-sync-graph-v2"
    assert graph.current_node == "materialize_sources"
    assert api_source is not None
    assert api_source.connection_id == connection.id
    assert api_source.selected_entities == ["teacher"]
    assert [source.source_role for source in sources] == ["target"]
    assert sources[0].storage_path == "database://seewo-mysql"
    assert [snapshot.source_role for snapshot in snapshots] == ["target"]
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

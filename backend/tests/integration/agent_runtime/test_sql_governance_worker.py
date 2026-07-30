import hashlib
import json
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.csv_rollback_handlers import _rollback_operation
from app.agent_runtime.database_mapping import (
    connector_with_frozen_database_mapping,
    load_frozen_database_mapping,
)
from app.agent_runtime.errors import ExternalWriteRecoveryRequired
from app.agent_runtime.sql_governance_handlers import SqlGovernanceExecutionHandler
from app.agent_runtime.sql_rollback_handlers import SqlRollbackExecutionHandler
from app.agent_runtime.worker import AgentWorkContext
from app.connectors.base import ConnectorVersion
from app.connectors.configured import (
    ConfiguredApiConnector,
    ConnectorCapabilities,
    ConnectorColumnSchema,
    ConnectorMutationResult,
    ConnectorSchema,
    DatabaseConnectorConfiguration,
)
from app.models.agent_analysis import (
    AgentFindingRecord,
    AgentGovernanceOperationRecord,
    AgentGovernancePlanRecord,
    AgentInputRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.models.agent_runtime import AgentCheckpointRecord, AgentRunRecord
from app.models.api_connectors import AgentSourceBindingRecord
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from tests.fixtures.connector_store import InMemoryConnectorStore


class StaticResolver:
    def __init__(
        self,
        connector: ConfiguredApiConnector,
        *,
        connector_id: str = "seewo-mysql",
    ) -> None:
        self._connector = connector
        self._connector_id = connector_id

    async def connector(self, connector_id: str) -> ConfiguredApiConnector:
        assert connector_id == self._connector_id
        return self._connector


class GeneratedKeyConnector:
    def __init__(
        self,
        configuration: DatabaseConnectorConfiguration,
        *,
        current_version: str,
        verification_result: bool = True,
        expected_identifier: str = "S002",
    ) -> None:
        self.configuration = configuration
        self._current_version = current_version
        self._verification_result = verification_result
        self._expected_identifier = expected_identifier
        self.verified_identifiers: list[str] = []

    async def discover_schema(self) -> ConnectorSchema:
        fields = tuple(sorted(self.configuration.allowed_columns))
        return ConnectorSchema(
            fields=fields,
            columns=tuple(
                ConnectorColumnSchema(
                    name=field,
                    sql_type="unknown",
                    nullable=True,
                    primary_key=False,
                    generated=False,
                    autoincrement=False,
                )
                for field in fields
            ),
        )

    def with_frozen_mapping(self, _mapping) -> "GeneratedKeyConnector":
        return self

    async def version(self) -> ConnectorVersion:
        return ConnectorVersion(value=self._current_version)

    async def apply(self, operations, *, idempotency_key: str, expected_version: str):
        assert operations[0]["id"] == self._expected_identifier
        assert idempotency_key
        assert expected_version == self._current_version
        self._current_version = "generated-key-version"
        return ConnectorMutationResult(
            version=ConnectorVersion(value=self._current_version),
            generated_identifiers=("41",),
        )

    async def verify(self, expected):
        self.verified_identifiers.extend(str(item["id"]) for item in expected)
        return [self._verification_result for _ in expected]


class ReadOnlyGeneratedKeyRecoveryConnector:
    def __init__(
        self,
        configuration: DatabaseConnectorConfiguration,
        *,
        current_version: str,
        generated_identifier: str = "42",
        verification_result: bool = True,
    ) -> None:
        self.configuration = configuration
        self._current_version = current_version
        self._generated_identifier = generated_identifier
        self._verification_result = verification_result
        self.apply_calls = 0
        self.verified_identifiers: list[str] = []

    async def discover_schema(self) -> ConnectorSchema:
        fields = tuple(sorted(self.configuration.allowed_columns))
        return ConnectorSchema(
            fields=fields,
            columns=tuple(
                ConnectorColumnSchema(
                    name=field,
                    sql_type="unknown",
                    nullable=True,
                    primary_key=False,
                    generated=False,
                    autoincrement=False,
                )
                for field in fields
            ),
        )

    def with_frozen_mapping(self, _mapping) -> "ReadOnlyGeneratedKeyRecoveryConnector":
        return self

    async def version(self) -> ConnectorVersion:
        return ConnectorVersion(value=self._current_version)

    async def apply(self, *_args, **_kwargs):
        self.apply_calls += 1
        raise AssertionError("version-mismatch recovery must never apply a create")

    async def verify(self, expected):
        self.verified_identifiers.extend(str(item["id"]) for item in expected)
        return [
            self._verification_result
            and str(item["id"]) == self._generated_identifier
            for item in expected
        ]


def _configuration_fingerprint(configuration: object) -> str:
    return hashlib.sha256(
        json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def _add_v3_database_mapping_facts(
    session,
    *,
    connector: ConfiguredApiConnector,
    configuration: DatabaseConnectorConfiguration,
    include_checkpoint: bool = True,
) -> tuple[ReconciliationTask, AgentRunRecord, dict[str, str]]:
    task = ReconciliationTask(
        tenant_id="school-v3",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="running",
        stage="governance",
        workflow_version="agent-graph-v1",
        agent_intent={
            "source": {"kind": "api", "configuration_id": "authority-api"},
            "target": {"kind": "database", "configuration_id": "seewo-data-mysql"},
        },
        idempotency_key=str(uuid4()),
        request_hash=uuid4().hex * 2,
    )
    session.add(task)
    await session.flush()
    run = AgentRunRecord(
        task_id=task.id,
        tenant_id=task.tenant_id,
        kind="sync",
        workflow_version="agent-graph-v1",
        ingestion_contract_version="source-ingestion-v3",
        phase="execute_and_verify",
        status="running",
    )
    session.add(run)
    await session.flush()
    authoritative_configuration = {"endpoint": "https://authority.example.test"}
    target_configuration = configuration.model_dump(mode="json")
    session.add_all(
        (
            AgentSourceBindingRecord(
                tenant_id=task.tenant_id,
                task_id=task.id,
                role="authoritative",
                connector_kind="api",
                configuration_id="authority-api",
                configuration_fingerprint=_configuration_fingerprint(
                    authoritative_configuration
                ),
                frozen_public_configuration=authoritative_configuration,
                credential_reference="secret://connectors/authority-api",
                mapping_checkpoint_key="graph-api-field-mapping-v3:authoritative",
                normalization_checkpoint_key="graph-source-normalization-v3:authoritative",
            ),
            AgentSourceBindingRecord(
                tenant_id=task.tenant_id,
                task_id=task.id,
                role="target",
                connector_kind="database",
                configuration_id="seewo-data-mysql",
                configuration_fingerprint=_configuration_fingerprint(
                    target_configuration
                ),
                frozen_public_configuration=target_configuration,
                credential_reference=configuration.credential_reference,
                mapping_checkpoint_key="graph-database-field-mapping-v3:target",
                normalization_checkpoint_key="graph-source-normalization-v3:target",
            ),
        )
    )
    mapping = {
        "category": "person_type",
        "name": "display_name",
        "number": "school_number",
        "class_name": "group_name",
        "phone": "mobile",
        "email": "mailbox",
    }
    if include_checkpoint:
        schema = await connector.discover_schema()
        schema_fingerprint = "sha256:" + _configuration_fingerprint(
            {
                "schema": schema.model_dump(mode="json"),
                "table": configuration.table_name,
                "primary_key": configuration.primary_key,
                "version_column": configuration.version_column,
            }
        )
        session.add(
            AgentCheckpointRecord(
                run_id=run.id,
                tenant_id=task.tenant_id,
                phase="ingest_and_normalize",
                checkpoint_key="graph-database-field-mapping-v3:target",
                input_hash=uuid4().hex * 2,
                status="completed",
                payload={
                    "schema_version": "source-ingestion-v3",
                    "mapping_version": "fixed-six-field-sql-mapping-v3",
                    "source_role": "target",
                    "connector_kind": "database",
                    "connector_id": "seewo-data-mysql",
                    "resolved": True,
                    "mapping": mapping,
                    "schema_fingerprint": schema_fingerprint,
                    "source_version": "v1",
                    "unresolved_required_fields": [],
                    "model_calls": 1,
                    "cache_hit": False,
                },
            )
        )
    await session.flush()
    return task, run, mapping


def _llm_mapped_connector() -> tuple[
    ConfiguredApiConnector,
    DatabaseConnectorConfiguration,
]:
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-data-mysql",
        dialect="mysql",
        database_name="seewo_data",
        table_name="data",
        primary_key="row_id",
        version_column="version",
        mapping={"mode": "llm"},
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
    return (
        ConfiguredApiConnector(
            configuration=configuration,
            store=InMemoryConnectorStore(
                records=[
                    {
                        "row_id": "1",
                        "version": "v1",
                        "person_type": "student",
                        "display_name": "张三",
                        "school_number": "S001",
                        "group_name": "一班",
                        "mobile": "13800000000",
                        "mailbox": "student@example.test",
                    }
                ]
            ),
        ),
        configuration,
    )


@pytest.mark.asyncio
async def test_v3_frozen_mapping_drives_database_execution_and_rollback(
    database,
) -> None:
    connector, configuration = _llm_mapped_connector()
    async with database.session_factory() as session:
        async with session.begin():
            task, run, expected_mapping = await _add_v3_database_mapping_facts(
                session,
                connector=connector,
                configuration=configuration,
            )

        async with session.begin():
            assert await load_frozen_database_mapping(
                session,
                task.id,
                run.id,
                "target",
            ) == expected_mapping
            _, bound, bound_configuration = (
                await connector_with_frozen_database_mapping(
                    session,
                    task_id=task.id,
                    run_id=run.id,
                    role="target",
                    connectors=StaticResolver(
                        connector,
                        connector_id="seewo-data-mysql",
                    ),
                )
            )

        assert bound_configuration.field_columns == expected_mapping
        initial_version = (await bound.version()).value
        updated = await bound.apply(
            [
                {
                    "operation": "update",
                    "id": "1",
                    "before": {"phone": "13800000000"},
                    "after": {"phone": "13800000001"},
                }
            ],
            idempotency_key="v3-update",
            expected_version=initial_version,
        )
        assert await bound.verify(
            [{"id": "1", "after": {"phone": "13800000001"}}]
        ) == [True]
        await bound.apply(
            [
                {
                    "operation": "update",
                    "id": "1",
                    "before": {"phone": "13800000001"},
                    "after": {"phone": "13800000000"},
                }
            ],
            idempotency_key="v3-rollback",
            expected_version=updated.value,
        )
        assert await bound.verify(
            [{"id": "1", "after": {"phone": "13800000000"}}]
        ) == [True]


@pytest.mark.asyncio
async def test_v3_missing_mapping_checkpoint_fails_before_database_mutation(
    database,
) -> None:
    connector, configuration = _llm_mapped_connector()
    async with database.session_factory() as session:
        async with session.begin():
            task, run, expected_mapping = await _add_v3_database_mapping_facts(
                session,
                connector=connector,
                configuration=configuration,
                include_checkpoint=False,
            )

        async with session.begin():
            with pytest.raises(
                ValueError,
                match="mapping checkpoint is missing",
            ):
                await connector_with_frozen_database_mapping(
                    session,
                    task_id=task.id,
                    run_id=run.id,
                    role="target",
                    connectors=StaticResolver(
                        connector,
                        connector_id="seewo-data-mysql",
                    ),
                )

        readable = connector.with_frozen_mapping(expected_mapping)
        assert (await readable.read_record("1"))["mobile"] == "13800000000"


@pytest.mark.asyncio
async def test_sql_governance_updates_mysql_target_and_verifies_result(
    database,
    monkeypatch,
) -> None:
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
    connector = ConfiguredApiConnector(
        configuration=configuration,
        store=InMemoryConnectorStore(
            records=[
                {
                    "id": "student-1",
                    "row_version": "v1",
                    "category": "student",
                    "name": "张三",
                    "number": "S001",
                    "class_name": "一班",
                    "phone": "13800000000",
                    "email": "student@example.test",
                }
            ]
        ),
    )
    async with database.session_factory() as session:
        async with session.begin():
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="governance",
                workflow_version="agent-graph-v1",
                agent_intent={
                    "source": {
                        "kind": "database",
                        "configuration_id": "authority-postgres",
                    },
                    "target": {
                        "kind": "database",
                        "configuration_id": "seewo-mysql",
                    },
                },
                idempotency_key=str(uuid4()),
                request_hash=uuid4().hex * 2,
            )
            session.add(task)
            await session.flush()
            source = SourceFile(
                task_id=task.id,
                source_role="target",
                original_name="seewo-mysql",
                storage_name=f"database-{uuid4().hex}",
                storage_path="database://seewo-mysql",
                managed_storage=False,
                sha256=uuid4().hex * 2,
                size_bytes=1,
            )
            session.add(source)
            await session.flush()
            snapshot = Snapshot(
                id=uuid4(),
                task_id=task.id,
                source_file_id=source.id,
                source_role="target",
                schema_version="agent-contract-v1",
                mapping_version="agent-sql-v2",
                file_hash=source.sha256,
                content_hash=uuid4().hex * 2,
                state="published",
                summary={},
            )
            session.add(snapshot)
            run = AgentRunRecord(
                task_id=task.id,
                tenant_id=task.tenant_id,
                kind="sync",
                workflow_version="agent-graph-v1",
                ingestion_contract_version="source-ingestion-v3",
                phase="execute_and_verify",
                status="running",
            )
            session.add(run)
            await session.flush()
            authoritative_configuration = configuration.model_copy(
                update={
                    "credential_reference": "secret://connectors/authority-postgres",
                    "source_role": "authoritative",
                }
            ).model_dump(mode="json")
            target_configuration = configuration.model_dump(mode="json")
            session.add_all(
                (
                    AgentSourceBindingRecord(
                        tenant_id=task.tenant_id,
                        task_id=task.id,
                        role="authoritative",
                        connector_kind="database",
                        configuration_id="authority-postgres",
                        configuration_fingerprint=_configuration_fingerprint(
                            authoritative_configuration
                        ),
                        frozen_public_configuration=authoritative_configuration,
                        credential_reference="secret://connectors/authority-postgres",
                        mapping_checkpoint_key=(
                            "graph-database-field-mapping-v3:authoritative"
                        ),
                        normalization_checkpoint_key=(
                            "graph-source-normalization-v3:authoritative"
                        ),
                    ),
                    AgentSourceBindingRecord(
                        tenant_id=task.tenant_id,
                        task_id=task.id,
                        role="target",
                        connector_kind="database",
                        configuration_id="seewo-mysql",
                        snapshot_id=snapshot.id,
                        configuration_fingerprint=_configuration_fingerprint(
                            target_configuration
                        ),
                        frozen_public_configuration=target_configuration,
                        credential_reference=configuration.credential_reference,
                        mapping_checkpoint_key="graph-database-field-mapping-v3:target",
                        normalization_checkpoint_key=(
                            "graph-source-normalization-v3:target"
                        ),
                    ),
                )
            )
            schema = await connector.discover_schema()
            session.add(
                AgentCheckpointRecord(
                    run_id=run.id,
                    tenant_id=task.tenant_id,
                    phase="ingest_and_normalize",
                    checkpoint_key="graph-database-field-mapping-v3:target",
                    input_hash=uuid4().hex * 2,
                    status="completed",
                    payload={
                        "schema_version": "source-ingestion-v3",
                        "mapping_version": "fixed-six-field-sql-mapping-v3",
                        "source_role": "target",
                        "connector_kind": "database",
                        "connector_id": "seewo-mysql",
                        "resolved": True,
                        "mapping": dict(configuration.field_columns),
                        "schema_fingerprint": "sha256:"
                        + _configuration_fingerprint(
                            {
                                "schema": schema.model_dump(mode="json"),
                                "table": configuration.table_name,
                                "primary_key": configuration.primary_key,
                                "version_column": configuration.version_column,
                            }
                        ),
                        "source_version": "v1",
                        "unresolved_required_fields": [],
                        "model_calls": 0,
                        "cache_hit": False,
                    },
                )
            )
            initial_version = TargetVersionRecord(
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_snapshot_id=snapshot.id,
                file_sha256=SqlGovernanceExecutionHandler.hash_version("v1"),
                content_hash=uuid4().hex * 2,
                storage_path=f"database://seewo-mysql/version/{uuid4().hex}",
            )
            session.add(initial_version)
            plan = AgentGovernancePlanRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_snapshot_id=snapshot.id,
                target_snapshot_id=snapshot.id,
                target_version=f"sha256:{initial_version.file_sha256}",
                finding_ids=[],
                operations=[],
                content_hash=uuid4().hex * 2,
                status="compiled",
                compiled_by="test",
            )
            session.add(plan)
            await session.flush()
            subject = AgentInputRecord(
                run_id=run.id,
                task_id=task.id,
                snapshot_id=snapshot.id,
                tenant_id=task.tenant_id,
                source_role="target",
                stable_locator="database:seewo-mysql:student-1",
                stable_order=1,
                entity_kind="student",
                category="student",
                name="张三",
                number="S001",
                class_name="一班",
                phone="13800000000",
                email="student@example.test",
                raw_row_number=None,
                input_hash=uuid4().hex * 2,
            )
            session.add(subject)
            await session.flush()
            work = AgentWorkItemRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_snapshot_id=snapshot.id,
                target_snapshot_id=snapshot.id,
                subject_input_id=subject.id,
                entity_kind="student",
                kind="field_difference",
                state="analyzed",
                idempotency_hash=uuid4().hex * 2,
                evidence_hash=uuid4().hex * 2,
            )
            session.add(work)
            batch = AgentModelBatchRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                entity_kind="student",
                input_hash=uuid4().hex * 2,
                item_count=1,
                status="completed",
            )
            session.add(batch)
            await session.flush()
            finding = AgentFindingRecord(
                run_id=run.id,
                task_id=task.id,
                work_item_id=work.id,
                batch_id=batch.id,
                kind="field_difference",
                category_zh="手机号不一致",
                analysis_zh="手机号与权威数据不一致。",
                evidence_refs=["test"],
                content_hash=uuid4().hex * 2,
            )
            session.add(finding)
            await session.flush()
            operation = AgentGovernanceOperationRecord(
                plan_id=plan.id,
                run_id=run.id,
                task_id=task.id,
                finding_id=finding.id,
                operation_type="update",
                entity_kind="student",
                target_source_identifier=("database:seewo-mysql:student-1"),
                before={"phone": "13800000000"},
                after={"phone": "13800000001"},
                dependencies=[],
                risk="high",
                status="pending",
                attempt_count=0,
            )
            session.add(operation)
            await session.flush()
            context = AgentWorkContext(
                worker_id="sql-worker",
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                phase="execute_and_verify",
                attempt_count=1,
                lease_token=uuid4(),
            )

        async with session.begin():
            result = await SqlGovernanceExecutionHandler(
                StaticResolver(connector)
            ).execute_operation(
                session,
                context,
                operation_id=operation.id,
            )

        assert result.status == "succeeded"
        assert result.actual_after == {"phone": "13800000001"}
        assert result.verification["valid"] is True
        assert (await connector.read_record("student-1"))["phone"] == "13800000001"

        output_version_id = UUID(str(result.verification["output_target_version_id"]))
        async with session.begin():
            rollback_task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="rollback",
                workflow_version="agent-graph-v1",
                task_kind="rollback",
                parent_task_id=task.id,
                agent_intent={
                    "source_task_id": str(task.id),
                    "target_version_id": str(output_version_id),
                    "source_mode": "database",
                    "target": {
                        "kind": "database",
                        "configuration_id": "seewo-mysql",
                    },
                    "operations": [
                        {
                            "id": str(operation.id),
                            "operation": "update",
                            "entity_kind": "student",
                            "target_source_identifier": ("database:seewo-mysql:student-1"),
                            "before": {"phone": "13800000000"},
                            "after": {"phone": "13800000001"},
                            "verification": {"valid": True},
                        }
                    ],
                },
                idempotency_key=str(uuid4()),
                request_hash=uuid4().hex * 2,
            )
            session.add(rollback_task)
            await session.flush()
            rollback_run = AgentRunRecord(
                task_id=rollback_task.id,
                tenant_id=rollback_task.tenant_id,
                kind="rollback",
                workflow_version="agent-graph-v1",
                ingestion_contract_version="source-ingestion-v3",
                phase="execute_restore",
                status="running",
            )
            session.add(rollback_run)
            await session.flush()
            rollback_context = AgentWorkContext(
                worker_id="sql-rollback-worker",
                run_id=rollback_run.id,
                task_id=rollback_task.id,
                tenant_id=rollback_task.tenant_id,
                phase="execute_restore",
                attempt_count=1,
                lease_token=uuid4(),
            )
            rollback_operation = _rollback_operation(
                rollback_task.agent_intent["operations"][0],
                target_version="ignored",
            )

        rollback_handler = SqlRollbackExecutionHandler(
            StaticResolver(connector)
        )
        async with session.begin():
            await rollback_handler.plan(
                session,
                rollback_context,
            )
            rollback_fact = await rollback_handler.execute_operation(
                session,
                rollback_context,
                rollback_operation.id,
            )

        assert rollback_fact["status"] == "succeeded"
        assert rollback_fact["verification"]["valid"] is True
        assert (await connector.read_record("student-1"))["phone"] == "13800000000"

        async with session.begin():
            replay_after_external_commit = AgentGovernanceOperationRecord(
                plan_id=plan.id,
                run_id=run.id,
                task_id=task.id,
                finding_id=finding.id,
                operation_type="update",
                entity_kind="student",
                target_source_identifier="database:seewo-mysql:student-1",
                before={"phone": "13800000000"},
                after={"phone": "13800000001"},
                dependencies=[],
                risk="high",
                status="pending",
                attempt_count=0,
            )
            session.add(replay_after_external_commit)
            await session.flush()

        external_version = (await connector.version()).value
        await connector.apply(
            [
                {
                    "operation": "update",
                    "id": "student-1",
                    "before": {"phone": "13800000000"},
                    "after": {"phone": "13800000001"},
                }
            ],
            idempotency_key="simulated-crash-after-external-commit",
            expected_version=external_version,
        )

        async with session.begin():
            recovered = await SqlGovernanceExecutionHandler(
                StaticResolver(connector)
            ).execute_operation(
                session,
                context,
                operation_id=replay_after_external_commit.id,
            )

        assert recovered.status == "succeeded"
        assert recovered.verification["idempotent_recovery"] is True
        assert (await connector.read_record("student-1"))["phone"] == "13800000001"

        recovered_version_id = UUID(
            str(recovered.verification["output_target_version_id"])
        )
        async with session.begin():
            recovered_rollback_task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="rollback",
                workflow_version="agent-graph-v1",
                task_kind="rollback",
                parent_task_id=task.id,
                agent_intent={
                    "source_task_id": str(task.id),
                    "target_version_id": str(recovered_version_id),
                    "source_mode": "database",
                    "target": {
                        "kind": "database",
                        "configuration_id": "seewo-mysql",
                    },
                    "operations": [
                        {
                            "id": str(replay_after_external_commit.id),
                            "operation": "update",
                            "entity_kind": "student",
                            "target_source_identifier": (
                                "database:seewo-mysql:student-1"
                            ),
                            "before": {"phone": "13800000000"},
                            "after": {"phone": "13800000001"},
                            "verification": {"valid": True},
                        }
                    ],
                },
                idempotency_key=str(uuid4()),
                request_hash=uuid4().hex * 2,
            )
            session.add(recovered_rollback_task)
            await session.flush()
            recovered_rollback_run = AgentRunRecord(
                task_id=recovered_rollback_task.id,
                tenant_id=recovered_rollback_task.tenant_id,
                kind="rollback",
                workflow_version="agent-graph-v1",
                ingestion_contract_version="source-ingestion-v3",
                phase="execute_restore",
                status="running",
            )
            session.add(recovered_rollback_run)
            await session.flush()
            recovered_rollback_context = AgentWorkContext(
                worker_id="sql-rollback-recovery-worker",
                run_id=recovered_rollback_run.id,
                task_id=recovered_rollback_task.id,
                tenant_id=recovered_rollback_task.tenant_id,
                phase="execute_restore",
                attempt_count=1,
                lease_token=uuid4(),
            )
            recovered_rollback_operation = _rollback_operation(
                recovered_rollback_task.agent_intent["operations"][0],
                target_version="ignored",
            )

        recovered_rollback_handler = SqlRollbackExecutionHandler(
            StaticResolver(connector)
        )
        async with session.begin():
            await recovered_rollback_handler.plan(
                session,
                recovered_rollback_context,
            )

        pre_rollback_version = (await connector.version()).value
        await connector.apply(
            [
                {
                    "operation": "update",
                    "id": "student-1",
                    "before": {"phone": "13800000001"},
                    "after": {"phone": "13800000000"},
                }
            ],
            idempotency_key="simulated-rollback-crash-after-external-commit",
            expected_version=pre_rollback_version,
        )

        async with session.begin():
            recovered_rollback = await recovered_rollback_handler.execute_operation(
                session,
                recovered_rollback_context,
                recovered_rollback_operation.id,
            )

        assert recovered_rollback["status"] == "already_restored"
        assert recovered_rollback["verification"]["idempotent_recovery"] is True
        assert (await connector.read_record("student-1"))["phone"] == "13800000000"

        generated_key_connector = GeneratedKeyConnector(
            configuration,
            current_version=(await connector.version()).value,
        )
        generated_create = AgentGovernanceOperationRecord(
            plan_id=plan.id,
            run_id=run.id,
            task_id=task.id,
            finding_id=finding.id,
            operation_type="create",
            entity_kind="student",
            target_source_identifier=None,
            before=None,
            after={"name": "王五", "number": "S002"},
            dependencies=[],
            risk="high",
            status="pending",
            attempt_count=0,
        )
        session.add(generated_create)
        await session.flush()
        generated_result = await SqlGovernanceExecutionHandler(
            StaticResolver(connector)
        ).execute_operation(
            session,
            context,
            operation_id=generated_create.id,
            connector_override=generated_key_connector,  # type: ignore[arg-type]
        )

        assert generated_result.status == "succeeded"
        assert generated_result.target_source_identifier == "database:seewo-mysql:41"
        assert generated_key_connector.verified_identifiers == ["41"]

        failed_verification_connector = GeneratedKeyConnector(
            configuration,
            current_version="generated-key-version",
            verification_result=False,
            expected_identifier="S003",
        )
        failed_verification_create = AgentGovernanceOperationRecord(
            plan_id=plan.id,
            run_id=run.id,
            task_id=task.id,
            finding_id=finding.id,
            operation_type="create",
            entity_kind="student",
            target_source_identifier=None,
            before=None,
            after={"name": "赵六", "number": "S003"},
            dependencies=[],
            risk="high",
            status="pending",
            attempt_count=0,
        )
        session.add(failed_verification_create)
        await session.flush()
        failed_verification = await SqlGovernanceExecutionHandler(
            StaticResolver(connector)
        ).execute_operation(
            session,
            context,
            operation_id=failed_verification_create.id,
            connector_override=failed_verification_connector,  # type: ignore[arg-type]
        )

        assert failed_verification.status == "verification_failed"
        assert (
            failed_verification.target_source_identifier == "database:seewo-mysql:41"
        )
        assert failed_verification.verification["target_source_identifier"] == (
            "database:seewo-mysql:41"
        )
        assert failed_verification.verification["target_version_value"] == (
            "generated-key-version"
        )

        await session.commit()
        ambiguous_recovery = AgentGovernanceOperationRecord(
            plan_id=plan.id,
            run_id=run.id,
            task_id=task.id,
            finding_id=finding.id,
            operation_type="create",
            entity_kind="student",
            target_source_identifier=None,
            before=None,
            after={"name": "钱七", "number": "S004"},
            dependencies=[],
            risk="high",
            status="pending",
            attempt_count=0,
        )
        session.add(ambiguous_recovery)
        await session.commit()
        fail_closed_connector = ReadOnlyGeneratedKeyRecoveryConnector(
            configuration,
            current_version="3",
        )

        async with session.begin():
            with pytest.raises(ExternalWriteRecoveryRequired):
                await SqlGovernanceExecutionHandler(
                    StaticResolver(connector)
                ).execute_operation(
                    session,
                    context,
                    operation_id=ambiguous_recovery.id,
                    connector_override=fail_closed_connector,  # type: ignore[arg-type]
                )

        assert fail_closed_connector.apply_calls == 0

        durable_recovery = AgentGovernanceOperationRecord(
            plan_id=plan.id,
            run_id=run.id,
            task_id=task.id,
            finding_id=finding.id,
            operation_type="create",
            entity_kind="student",
            target_source_identifier="database:seewo-mysql:42",
            before=None,
            after={"name": "钱七", "number": "S004"},
            dependencies=[],
            risk="high",
            status="pending",
            attempt_count=0,
            verification={
                "target_source_identifier": "database:seewo-mysql:42",
                "target_version_value": "2",
            },
        )
        session.add(durable_recovery)
        await session.commit()
        read_only_connector = ReadOnlyGeneratedKeyRecoveryConnector(
            configuration,
            current_version="3",
        )

        async with session.begin():
            recovered_create = await SqlGovernanceExecutionHandler(
                StaticResolver(connector)
            ).execute_operation(
                session,
                context,
                operation_id=durable_recovery.id,
                connector_override=read_only_connector,  # type: ignore[arg-type]
            )

        assert recovered_create.status == "succeeded"
        assert recovered_create.target_source_identifier == "database:seewo-mysql:42"
        assert recovered_create.verification["idempotent_recovery"] is True
        assert recovered_create.verification["target_version_value"] == "2"
        assert recovered_create.verification["target_version_after"] == (
            SqlGovernanceExecutionHandler.hash_version("2")
        )
        assert read_only_connector.apply_calls == 0
        assert read_only_connector.verified_identifiers == ["42"]

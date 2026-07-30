from uuid import UUID, uuid4

import pytest

from app.agent_runtime.csv_rollback_handlers import _rollback_operation
from app.agent_runtime.sql_governance_handlers import SqlGovernanceExecutionHandler
from app.agent_runtime.sql_rollback_handlers import SqlRollbackExecutionHandler
from app.agent_runtime.worker import AgentWorkContext
from app.connectors.base import ConnectorVersion
from app.connectors.configured import (
    ConfiguredApiConnector,
    ConnectorCapabilities,
    ConnectorMutationResult,
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
from app.models.agent_runtime import AgentRunRecord
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.executions import ExecutionRepository
from tests.fixtures.connector_store import InMemoryConnectorStore


class StaticResolver:
    def __init__(self, connector: ConfiguredApiConnector) -> None:
        self._connector = connector

    async def connector(self, connector_id: str) -> ConfiguredApiConnector:
        assert connector_id == "seewo-mysql"
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


class DurableGeneratedKeyConnector:
    def __init__(
        self,
        configuration: DatabaseConnectorConfiguration,
        state: dict[str, object],
    ) -> None:
        self.configuration = configuration
        self._state = state

    async def version(self) -> ConnectorVersion:
        return ConnectorVersion(value=str(self._state["version"]))

    async def apply(self, operations, *, idempotency_key: str, expected_version: str):
        assert operations[0]["id"] == "S004"
        assert expected_version == self._state["version"]
        self._state["apply_calls"] = int(self._state.get("apply_calls", 0)) + 1
        self._state.setdefault("receipt", idempotency_key)
        if self._state["receipt"] == idempotency_key:
            self._state["version"] = "durable-generated-key-version"
        return ConnectorMutationResult(
            version=ConnectorVersion(value=str(self._state["version"])),
            generated_identifiers=("42",),
        )

    async def verify(self, expected):
        return [str(item["id"]) == "42" for item in expected]


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
                phase="execute_and_verify",
                status="running",
            )
            session.add(run)
            await session.flush()
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

        await session.commit()
        durable_state: dict[str, object] = {"version": "generated-key-version"}
        recovery_operation = AgentGovernanceOperationRecord(
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
        session.add(recovery_operation)
        await session.commit()
        original_create_target_version = ExecutionRepository.create_target_version

        async def fail_target_version(*_args, **_kwargs):
            raise ValueError("metadata commit failed")

        monkeypatch.setattr(
            ExecutionRepository,
            "create_target_version",
            fail_target_version,
        )
        async with session.begin():
            with pytest.raises(ValueError, match="metadata commit failed"):
                await SqlGovernanceExecutionHandler(
                    StaticResolver(connector)
                ).execute_operation(
                    session,
                    context,
                    operation_id=recovery_operation.id,
                    connector_override=DurableGeneratedKeyConnector(
                        configuration,
                        durable_state,
                    ),  # type: ignore[arg-type]
                )

        monkeypatch.setattr(
            ExecutionRepository,
            "create_target_version",
            original_create_target_version,
        )
        async with session.begin():
            recovered_create = await SqlGovernanceExecutionHandler(
                StaticResolver(connector)
            ).execute_operation(
                session,
                context,
                operation_id=recovery_operation.id,
                connector_override=DurableGeneratedKeyConnector(
                    configuration,
                    durable_state,
                ),  # type: ignore[arg-type]
            )

        assert recovered_create.status == "succeeded"
        assert recovered_create.target_source_identifier == "database:seewo-mysql:42"
        assert durable_state["apply_calls"] == 2

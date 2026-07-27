"""Deterministic SQL rollback over verified mutation facts."""

import hashlib
import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.csv_rollback_handlers import (
    _rollback_no_write_fact,
    _rollback_operation,
    _rollback_operations,
    compare_csv_rollback_mutation,
)
from app.agent_runtime.errors import ExternalWriteRecoveryRequired
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase
from app.agent_runtime.worker import AgentWorkContext, AgentWorkResult
from app.connectors.configured import (
    ConfiguredApiConnector,
    DatabaseConnectorConfiguration,
)
from app.connectors.database_runtime import DatabaseConnectorResolver
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.repositories.executions import ExecutionRepository


class SqlRollbackExecutionHandler:
    def __init__(self, connectors: DatabaseConnectorResolver) -> None:
        self._connectors = connectors

    async def plan(
        self,
        session: AsyncSession,
        context: AgentWorkContext,
    ) -> AgentWorkResult:
        task = await session.get(ReconciliationTask, context.task_id)
        if task is None or not isinstance(task.agent_intent, dict):
            raise LookupError("SQL rollback task facts are missing")
        connector_id, connector, configuration = await self._connector_for_task(
            task
        )
        initial_parent = await session.get(
            TargetVersionRecord,
            UUID(str(task.agent_intent["target_version_id"])),
        )
        if initial_parent is None:
            raise LookupError("SQL rollback target version is missing")
        current_parent = await ExecutionRepository(
            session
        ).current_target_version(initial_parent.task_id)
        if current_parent is None:
            raise LookupError("current SQL rollback target version is missing")

        operations = tuple(
            dict(item) for item in task.agent_intent.get("operations", [])
        )
        restore_comparisons: list[dict[str, object]] = []
        for mutation in operations:
            operation = _rollback_operation(
                mutation,
                target_version=f"sha256:{initial_parent.file_sha256}",
            )
            identifier = _rollback_identifier(
                connector_id=connector_id,
                operation=str(operation.operation),
                locator=operation.target_source_identifier,
                after=operation.after,
            )
            current = _database_comparison_record(
                configuration,
                mutation=mutation,
                identifier=identifier,
                record=await connector.read_record(identifier),
            )
            restore_comparisons.append(
                compare_csv_rollback_mutation(
                    mutation,
                    current=current,
                    complete_record_fields=_database_complete_record_fields(
                        configuration
                    ),
                )
            )

        external_version = (await connector.version()).value
        updated_intent = dict(task.agent_intent)
        updated_intent["restore_comparisons"] = restore_comparisons
        updated_intent["comparison_target_version_id"] = str(
            current_parent.id
        )
        updated_intent["comparison_external_version_hash"] = _hash_version(
            external_version
        )
        task.agent_intent = updated_intent
        await AgentRuntimeRepository(session).save_checkpoint(
            context.run_id,
            phase=AgentPhase.PLAN_RESTORE,
            checkpoint_key="agent-sql-rollback-plan-v2",
            input_hash=str(task.request_hash),
            payload={
                "source_task_id": str(task.parent_task_id),
                "target_version_id": str(initial_parent.id),
                "comparison_target_version_id": str(current_parent.id),
                "comparison_external_version_hash": _hash_version(
                    external_version
                ),
                "operations": list(operations),
                "restore_comparisons": restore_comparisons,
            },
        )
        return AgentWorkResult(
            next_phase=AgentPhase.CLARIFY_RESTORE_CONFLICTS
        )

    async def execute_operation(
        self,
        session: AsyncSession,
        context: AgentWorkContext,
        operation_id: UUID,
    ) -> dict[str, object]:
        runtime = AgentRuntimeRepository(session)
        checkpoint_key = f"agent-sql-rollback-operation:{operation_id}"
        existing = await runtime.get_checkpoint(
            context.run_id,
            phase=AgentPhase.EXECUTE_RESTORE,
            checkpoint_key=checkpoint_key,
        )
        if existing is not None:
            return dict(existing.payload)

        task = await session.get(ReconciliationTask, context.task_id)
        if task is None or not isinstance(task.agent_intent, dict):
            raise LookupError("SQL rollback task facts are missing")
        connector_id, connector, configuration = await self._connector_for_task(
            task
        )

        initial_parent = await session.get(
            TargetVersionRecord,
            UUID(str(task.agent_intent["target_version_id"])),
        )
        if initial_parent is None:
            raise LookupError("SQL rollback target version is missing")
        mutation_facts = tuple(dict(item) for item in task.agent_intent.get("operations", []))
        frozen = _rollback_operations(
            mutation_facts,
            target_version=f"sha256:{initial_parent.file_sha256}",
        )
        operation_by_id = {item.id: item for item in frozen}
        selected = operation_by_id.get(operation_id)
        if selected is None:
            raise ValueError("SQL rollback operation is outside the frozen plan")
        mutation_by_operation_id = {
            _rollback_operation(
                mutation,
                target_version=f"sha256:{initial_parent.file_sha256}",
            ).id: mutation
            for mutation in mutation_facts
        }
        for dependency_id in selected.dependencies:
            dependency = await runtime.get_checkpoint(
                context.run_id,
                phase=AgentPhase.EXECUTE_RESTORE,
                checkpoint_key=(
                    f"agent-sql-rollback-operation:{dependency_id}"
                ),
            )
            if dependency is None:
                raise ValueError("SQL rollback dependency is not ready")
            if dependency.payload.get("status") not in {
                "succeeded",
                "already_restored",
            }:
                blocked: dict[str, object] = {
                    "id": str(operation_id),
                    "status": "blocked",
                    "verification": {"valid": False},
                    "compensation_for": str(selected.finding_id),
                    "safe_error_code": "rollback_dependency_failed",
                }
                await runtime.save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.EXECUTE_RESTORE,
                    checkpoint_key=checkpoint_key,
                    input_hash=str(task.request_hash),
                    payload=blocked,
                )
                return blocked

        selected_mutation = mutation_by_operation_id[operation_id]
        before = _fixed_values(selected.before)
        after = _fixed_values(selected.after)
        operation_name = str(selected.operation)
        identifier = _rollback_identifier(
            connector_id=connector_id,
            operation=operation_name,
            locator=selected.target_source_identifier,
            after=selected.after,
        )
        parent = await ExecutionRepository(session).current_target_version(
            initial_parent.task_id
        )
        planned_comparison = next(
            (
                dict(item)
                for item in task.agent_intent.get(
                    "restore_comparisons",
                    [],
                )
                if str(item.get("operation_id"))
                == str(selected_mutation["id"])
            ),
            None,
        )
        current_record = await connector.read_record(identifier)
        current_comparison = compare_csv_rollback_mutation(
            selected_mutation,
            current=_database_comparison_record(
                configuration,
                mutation=selected_mutation,
                identifier=identifier,
                record=current_record,
            ),
            complete_record_fields=_database_complete_record_fields(
                configuration
            ),
        )
        if planned_comparison is None or parent is None:
            fact = _rollback_no_write_fact(
                selected,
                status="conflict_skipped",
                comparison=current_comparison,
                safe_error_code="rollback_comparison_fact_missing",
            )
            await runtime.save_checkpoint(
                context.run_id,
                phase=AgentPhase.EXECUTE_RESTORE,
                checkpoint_key=checkpoint_key,
                input_hash=str(task.request_hash),
                payload=fact,
            )
            return fact

        raw_version = (await connector.version()).value
        if current_comparison["disposition"] == "already_restored":
            recovered = _rollback_no_write_fact(
                selected,
                status="already_restored",
                comparison=current_comparison,
            )
            if _hash_version(raw_version) != parent.file_sha256:
                output_hash = _hash_version(raw_version)
                try:
                    output = await ExecutionRepository(
                        session
                    ).create_target_version(
                        task_id=parent.task_id,
                        tenant_id=parent.tenant_id,
                        source_snapshot_id=parent.source_snapshot_id,
                        parent_version_id=parent.id,
                        batch_id=None,
                        file_sha256=output_hash,
                        content_hash=_hash(
                            {
                                "rollback_task_id": str(task.id),
                                "operation_id": str(selected.id),
                                "recovered_external_version": raw_version,
                            }
                        ),
                        storage_path=(
                            "database://"
                            f"{connector_id}/rollback/{output_hash}/"
                            f"{selected.id}/recovered"
                        ),
                    )
                except Exception as error:
                    raise ExternalWriteRecoveryRequired(
                        "SQL rollback recovery fact must be replayed"
                    ) from error
                recovered.update(
                    {
                        "output_target_version_id": str(output.id),
                        "output_target_path": output.storage_path,
                    }
                )
                verification_value = recovered.get("verification")
                verification = (
                    dict(verification_value)
                    if isinstance(verification_value, dict)
                    else {}
                )
                verification["idempotent_recovery"] = True
                verification["output_target_version_id"] = str(output.id)
                recovered["verification"] = verification
            await runtime.save_checkpoint(
                context.run_id,
                phase=AgentPhase.EXECUTE_RESTORE,
                checkpoint_key=checkpoint_key,
                input_hash=str(task.request_hash),
                payload=recovered,
            )
            return recovered

        if not (
            planned_comparison.get("disposition") == "safe_to_restore"
            and current_comparison["disposition"] == "safe_to_restore"
            and current_comparison["comparison_hash"]
            == planned_comparison.get("comparison_hash")
        ):
            fact = _rollback_no_write_fact(
                selected,
                status="conflict_skipped",
                comparison=current_comparison,
                safe_error_code="rollback_current_data_conflict",
            )
            await runtime.save_checkpoint(
                context.run_id,
                phase=AgentPhase.EXECUTE_RESTORE,
                checkpoint_key=checkpoint_key,
                input_hash=str(task.request_hash),
                payload=fact,
            )
            return fact

        output_version = await connector.apply(
            [
                {
                    "operation": operation_name,
                    "id": identifier,
                    "before": before,
                    "after": after,
                }
            ],
            idempotency_key=f"rollback:{task.id}:{selected.id}",
            expected_version=raw_version,
        )
        if operation_name == "delete":
            verified = await connector.read_record(identifier) is None
        else:
            verified = (
                await connector.verify(
                    [{"id": identifier, "after": after}]
                )
            ) == [True]
        output_hash = _hash_version(output_version.value)
        try:
            output = await ExecutionRepository(
                session
            ).create_target_version(
                task_id=parent.task_id,
                tenant_id=parent.tenant_id,
                source_snapshot_id=parent.source_snapshot_id,
                parent_version_id=parent.id,
                batch_id=None,
                file_sha256=output_hash,
                content_hash=_hash(
                    {
                        "rollback_task_id": str(task.id),
                        "operation_id": str(selected.id),
                        "before_version": raw_version,
                        "after_version": output_version.value,
                    }
                ),
                storage_path=(
                    f"database://{connector_id}/rollback/"
                    f"{output_hash}/{selected.id}"
                ),
            )
        except Exception as error:
            raise ExternalWriteRecoveryRequired(
                "SQL rollback external write must be replayed"
            ) from error
        final_fact: dict[str, object] = {
            "id": str(selected.id),
            "status": "succeeded" if verified else "verification_failed",
            "operation": operation_name,
            "entity_kind": selected.entity_kind,
            "target_source_identifier": (
                f"database:{connector_id}:{identifier}"
            ),
            "before": before or None,
            "after": after or None,
            "verification": {
                "valid": verified,
                "connector_id": connector_id,
                "output_target_version_id": str(output.id),
            },
            "compensation_for": str(selected.finding_id),
            "output_target_version_id": str(output.id),
            "output_target_path": output.storage_path,
        }
        await runtime.save_checkpoint(
            context.run_id,
            phase=AgentPhase.EXECUTE_RESTORE,
            checkpoint_key=checkpoint_key,
            input_hash=str(task.request_hash),
            payload=final_fact,
        )
        return final_fact

    async def _connector_for_task(
        self,
        task: ReconciliationTask,
    ) -> tuple[
        str,
        ConfiguredApiConnector,
        DatabaseConnectorConfiguration,
    ]:
        intent = task.agent_intent
        if not isinstance(intent, dict):
            raise LookupError("SQL rollback task facts are missing")
        target = intent.get("target")
        if not isinstance(target, dict) or target.get("kind") != "database":
            raise ValueError("SQL rollback target selection is missing")
        connector_id = target.get("configuration_id")
        if not isinstance(connector_id, str) or not connector_id:
            raise ValueError("SQL rollback target connector ID is missing")
        connector = await self._connectors.connector(connector_id)
        configuration = connector.configuration
        if not isinstance(configuration, DatabaseConnectorConfiguration):
            raise TypeError("SQL rollback resolved a non-database connector")
        return connector_id, connector, configuration


def _database_comparison_record(
    configuration: DatabaseConnectorConfiguration,
    *,
    mutation: dict[str, object],
    identifier: str,
    record: dict[str, object] | None,
) -> dict[str, object] | None:
    if record is None:
        return None
    current = {
        canonical: record.get(column)
        for canonical, column in configuration.field_columns.items()
    }
    mapped_columns = set(configuration.field_columns.values())
    for column in configuration.allowed_columns:
        if column not in {
            configuration.primary_key,
            configuration.version_column,
            *mapped_columns,
        }:
            current[column] = record.get(column)
    locator = mutation.get("target_source_identifier")
    after = mutation.get("after")
    after_values = after if isinstance(after, dict) else {}
    current["source_id"] = str(
        locator
        or after_values.get("source_id")
        or identifier
    )
    return current


def _database_complete_record_fields(
    configuration: DatabaseConnectorConfiguration,
) -> set[str]:
    mapped_columns = set(configuration.field_columns.values())
    custom_columns = set(configuration.allowed_columns) - {
        configuration.primary_key,
        configuration.version_column,
        *mapped_columns,
    }
    return set(configuration.field_columns) | custom_columns


def _rollback_identifier(
    *,
    connector_id: str,
    operation: str,
    locator: str | None,
    after: object,
) -> str:
    prefix = f"database:{connector_id}:"
    if operation == "create":
        values = after if isinstance(after, dict) else {}
        candidate = values.get("source_id") or values.get("number")
        if isinstance(candidate, str) and candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
        if candidate is None or not str(candidate).strip():
            raise ValueError("SQL rollback create lacks a stable identifier")
        return str(candidate).strip()
    if not isinstance(locator, str):
        raise ValueError("SQL rollback mutation lacks a target locator")
    if locator.startswith(prefix):
        return locator[len(prefix) :]
    if ":" not in locator:
        return locator
    raise ValueError("SQL rollback target locator belongs to another connector")


def _fixed_values(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    allowed = {"category", "name", "number", "class_name", "phone", "email"}
    return {str(field): item for field, item in value.items() if field in allowed}


def _hash_version(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

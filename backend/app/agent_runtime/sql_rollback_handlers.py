"""Deterministic SQL rollback over verified mutation facts."""

import hashlib
import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.csv_rollback_handlers import _rollback_operation
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase
from app.agent_runtime.worker import AgentWorkContext
from app.connectors.configured import DatabaseConnectorConfiguration
from app.connectors.database_runtime import DatabaseConnectorResolver
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.repositories.executions import ExecutionRepository


class SqlRollbackExecutionHandler:
    def __init__(self, connectors: DatabaseConnectorResolver) -> None:
        self._connectors = connectors

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
        target = task.agent_intent.get("target")
        if not isinstance(target, dict) or target.get("kind") != "database":
            raise ValueError("SQL rollback target selection is missing")
        connector_id = target.get("configuration_id")
        if not isinstance(connector_id, str) or not connector_id:
            raise ValueError("SQL rollback target connector ID is missing")
        connector = await self._connectors.connector(connector_id)
        configuration = connector.configuration
        if not isinstance(configuration, DatabaseConnectorConfiguration):
            raise TypeError("SQL rollback resolved a non-database connector")

        initial_parent = await session.get(
            TargetVersionRecord,
            UUID(str(task.agent_intent["target_version_id"])),
        )
        if initial_parent is None:
            raise LookupError("SQL rollback target version is missing")
        mutation_facts = tuple(dict(item) for item in task.agent_intent.get("operations", []))
        frozen = tuple(
            _rollback_operation(
                item,
                target_version=f"sha256:{initial_parent.file_sha256}",
            )
            for item in mutation_facts
        )
        index_by_id = {item.id: index for index, item in enumerate(frozen)}
        selected_index = index_by_id.get(operation_id)
        if selected_index is None:
            raise ValueError("SQL rollback operation is outside the frozen plan")

        parent = initial_parent
        if selected_index:
            previous_id = frozen[selected_index - 1].id
            previous = await runtime.get_checkpoint(
                context.run_id,
                phase=AgentPhase.EXECUTE_RESTORE,
                checkpoint_key=f"agent-sql-rollback-operation:{previous_id}",
            )
            if previous is None:
                raise ValueError("SQL rollback dependency is not ready")
            if previous.payload.get("status") != "succeeded":
                blocked_fact: dict[str, object] = {
                    "id": str(operation_id),
                    "status": "blocked",
                    "verification": {"valid": False},
                    "compensation_for": str(frozen[selected_index].finding_id),
                    "safe_error_code": "rollback_dependency_failed",
                }
                await runtime.save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.EXECUTE_RESTORE,
                    checkpoint_key=checkpoint_key,
                    input_hash=str(task.request_hash),
                    payload=blocked_fact,
                )
                return blocked_fact
            output_version_id = previous.payload.get("output_target_version_id")
            if output_version_id is None:
                raise LookupError("SQL rollback dependency version is missing")
            dependency_parent = await session.get(
                TargetVersionRecord,
                UUID(str(output_version_id)),
            )
            if dependency_parent is None:
                raise LookupError("SQL rollback dependency version is missing")
            parent = dependency_parent

        selected = frozen[selected_index]
        before = _fixed_values(selected.before)
        after = _fixed_values(selected.after)
        operation_name = str(selected.operation)
        identifier = _rollback_identifier(
            connector_id=connector_id,
            operation=operation_name,
            locator=selected.target_source_identifier,
            after=selected.after,
        )
        raw_version = (await connector.version()).value
        if _hash_version(raw_version) != parent.file_sha256:
            if operation_name == "delete":
                already_applied = await connector.read_record(identifier) is None
            else:
                already_applied = (
                    await connector.verify([{"id": identifier, "after": after}])
                ) == [True]
            if not already_applied:
                raise ValueError("SQL rollback target has an intervening change")
            output_hash = _hash_version(raw_version)
            output = await ExecutionRepository(session).create_target_version(
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
                    f"database://{connector_id}/rollback/{output_hash}/{selected.id}/recovered"
                ),
            )
            recovered_fact: dict[str, object] = {
                "id": str(selected.id),
                "status": "succeeded",
                "operation": operation_name,
                "entity_kind": selected.entity_kind,
                "target_source_identifier": f"database:{connector_id}:{identifier}",
                "before": before or None,
                "after": after or None,
                "verification": {
                    "valid": True,
                    "idempotent_recovery": True,
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
                payload=recovered_fact,
            )
            return recovered_fact
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
            verified = (await connector.verify([{"id": identifier, "after": after}])) == [True]
        output_hash = _hash_version(output_version.value)
        output = await ExecutionRepository(session).create_target_version(
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
            storage_path=(f"database://{connector_id}/rollback/{output_hash}/{selected.id}"),
        )
        final_fact: dict[str, object] = {
            "id": str(selected.id),
            "status": "succeeded" if verified else "verification_failed",
            "operation": operation_name,
            "entity_kind": selected.entity_kind,
            "target_source_identifier": (f"database:{connector_id}:{identifier}"),
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

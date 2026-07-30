"""Deterministic SQL governance execution over frozen Agent operations."""

import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.worker import AgentWorkContext
from app.connectors.configured import (
    ConfiguredApiConnector,
    ConnectorCapabilityError,
    ConnectorConflictError,
    DatabaseConnectorConfiguration,
)
from app.connectors.database_runtime import DatabaseConnectorResolver
from app.models.agent_analysis import (
    AgentGovernanceOperationRecord,
    AgentGovernancePlanRecord,
)
from app.models.reconciliation import ReconciliationTask
from app.repositories.agent_governance import AgentGovernanceRepository
from app.repositories.executions import ExecutionRepository


class SqlGovernanceExecutionHandler:
    """Apply one approved operation to the configured writable SQL target."""

    def __init__(self, connectors: DatabaseConnectorResolver) -> None:
        self._connectors = connectors

    @staticmethod
    def hash_version(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    async def execute_operation(
        self,
        session: AsyncSession,
        context: AgentWorkContext,
        *,
        operation_id: UUID,
        connector_override: ConfiguredApiConnector | None = None,
    ) -> AgentGovernanceOperationRecord:
        operation = await session.scalar(
            select(AgentGovernanceOperationRecord)
            .where(
                AgentGovernanceOperationRecord.id == operation_id,
                AgentGovernanceOperationRecord.run_id == context.run_id,
                AgentGovernanceOperationRecord.task_id == context.task_id,
            )
            .with_for_update()
        )
        if operation is None:
            raise LookupError("SQL governance operation is missing")
        if operation.status in {
            "succeeded",
            "failed",
            "blocked",
            "verification_failed",
        }:
            return operation
        plan = await session.get(AgentGovernancePlanRecord, operation.plan_id)
        if plan is None:
            raise LookupError("SQL governance plan is missing")
        dependency_ids = tuple(UUID(str(value)) for value in operation.dependencies)
        if dependency_ids:
            dependencies = tuple(
                await session.scalars(
                    select(AgentGovernanceOperationRecord).where(
                        AgentGovernanceOperationRecord.id.in_(dependency_ids),
                        AgentGovernanceOperationRecord.plan_id == plan.id,
                    )
                )
            )
            if len(dependencies) != len(dependency_ids):
                raise ValueError("SQL governance dependency is missing")
            if any(item.status != "succeeded" for item in dependencies):
                return await AgentGovernanceRepository(session).record_operation_outcome(
                    operation.id,
                    status="blocked",
                    attempts=0,
                    error_code="dependency_failed",
                )

        task = await session.get(ReconciliationTask, context.task_id)
        if task is None or not isinstance(task.agent_intent, dict):
            raise LookupError("SQL Agent task intent is missing")
        target = task.agent_intent.get("target")
        if not isinstance(target, dict) or target.get("kind") != "database":
            raise ValueError("SQL Agent target selection changed")
        connector_id = target.get("configuration_id")
        if not isinstance(connector_id, str) or not connector_id:
            raise ValueError("SQL Agent target connector ID is missing")
        connector = connector_override or await self._connectors.connector(connector_id)
        configuration = connector.configuration
        if (
            not isinstance(configuration, DatabaseConnectorConfiguration)
            or configuration.source_role != "target"
        ):
            raise ConnectorCapabilityError("SQL governance requires a configured target connector")

        executions = ExecutionRepository(session)
        parent = await executions.current_target_version(context.task_id)
        if parent is None or not parent.storage_path.startswith(f"database://{connector_id}/"):
            raise LookupError("SQL target version is missing")
        before = _fixed_values(operation.before)
        after = _fixed_values(operation.after)
        identifier = _operation_identifier(
            connector_id=connector_id,
            operation=operation,
        )
        raw_version = (await connector.version()).value
        if self.hash_version(raw_version) != parent.file_sha256:
            if operation.operation_type == "delete":
                already_applied = await connector.read_record(identifier) is None
            else:
                already_applied = (
                    await connector.verify([{"id": identifier, "after": after}])
                ) == [True]
            if not already_applied:
                raise ConnectorConflictError("SQL target version changed outside the plan")
            output_hash = self.hash_version(raw_version)
            output_target = await executions.create_target_version(
                task_id=context.task_id,
                tenant_id=context.tenant_id,
                source_snapshot_id=plan.target_snapshot_id,
                parent_version_id=parent.id,
                batch_id=None,
                file_sha256=output_hash,
                content_hash=_hash(
                    {
                        "operation_id": str(operation.id),
                        "operation": operation.operation_type,
                        "recovered_external_version": raw_version,
                    }
                ),
                storage_path=(
                    f"database://{connector_id}/version/{output_hash}/{operation.id}/recovered"
                ),
            )
            if operation.operation_type == "create":
                operation.target_source_identifier = f"database:{connector_id}:{identifier}"
            return await AgentGovernanceRepository(session).record_operation_outcome(
                operation.id,
                status="succeeded",
                attempts=0,
                actual_after=after if operation.operation_type != "delete" else None,
                verification={
                    "valid": True,
                    "idempotent_recovery": True,
                    "connector_id": connector_id,
                    "target_version_after": output_hash,
                    "output_target_version_id": str(output_target.id),
                },
            )
        prior_success = await session.scalar(
            select(AgentGovernanceOperationRecord.id).where(
                AgentGovernanceOperationRecord.plan_id == plan.id,
                AgentGovernanceOperationRecord.status == "succeeded",
            )
        )
        if prior_success is None and plan.target_version != f"sha256:{parent.file_sha256}":
            raise ConnectorConflictError("SQL target version is stale")

        connector_operation: dict[str, object] = {
            "operation": operation.operation_type,
            "id": identifier,
            "before": before,
            "after": after,
        }
        output_version = await connector.apply(
            [connector_operation],
            idempotency_key=f"{plan.id}:{operation.id}",
            expected_version=raw_version,
        )
        verified_identifier = _mutation_identifier(
            operation=operation,
            requested_identifier=identifier,
            generated_identifiers=output_version.generated_identifiers,
        )
        if operation.operation_type == "delete":
            verified = await connector.read_record(identifier) is None
        else:
            verified = (
                await connector.verify([{"id": verified_identifier, "after": after}])
            ) == [True]
        status = "succeeded" if verified else "verification_failed"
        output_hash = self.hash_version(output_version.value)
        output_target = await executions.create_target_version(
            task_id=context.task_id,
            tenant_id=context.tenant_id,
            source_snapshot_id=plan.target_snapshot_id,
            parent_version_id=parent.id,
            batch_id=None,
            file_sha256=output_hash,
            content_hash=_hash(
                {
                    "operation_id": str(operation.id),
                    "operation": operation.operation_type,
                    "before_version": raw_version,
                    "after_version": output_version.value,
                }
            ),
            storage_path=(f"database://{connector_id}/version/{output_hash}/{operation.id}"),
        )
        if operation.operation_type == "create" and verified:
            operation.target_source_identifier = (
                f"database:{connector_id}:{verified_identifier}"
            )
        return await AgentGovernanceRepository(session).record_operation_outcome(
            operation.id,
            status=status,
            attempts=1,
            actual_after=after if operation.operation_type != "delete" else None,
            verification={
                "valid": verified,
                "connector_id": connector_id,
                "target_version_before": self.hash_version(raw_version),
                "target_version_after": output_hash,
                "output_target_version_id": str(output_target.id),
            },
            error_code=None if verified else "target_verification_failed",
        )


def _operation_identifier(
    *,
    connector_id: str,
    operation: AgentGovernanceOperationRecord,
) -> str:
    if operation.operation_type == "create":
        after = operation.after or {}
        identifier = after.get("source_id") or after.get("number")
        if identifier is None or not str(identifier).strip():
            raise ValueError("SQL create operation lacks a stable identifier")
        return str(identifier).strip()
    locator = operation.target_source_identifier
    prefix = f"database:{connector_id}:"
    if not isinstance(locator, str) or not locator.startswith(prefix):
        raise ValueError("SQL mutation target locator is invalid")
    identifier = locator[len(prefix) :]
    if not identifier:
        raise ValueError("SQL mutation target locator lacks an identifier")
    return identifier


def _mutation_identifier(
    *,
    operation: AgentGovernanceOperationRecord,
    requested_identifier: str,
    generated_identifiers: tuple[str | None, ...],
) -> str:
    if operation.operation_type != "create":
        return requested_identifier
    if len(generated_identifiers) != 1:
        raise ConnectorConflictError("SQL create mutation result is incomplete")
    return generated_identifiers[0] or requested_identifier


def _fixed_values(value: dict[str, object] | None) -> dict[str, object]:
    allowed = {"category", "name", "number", "class_name", "phone", "email"}
    return {str(field): item for field, item in (value or {}).items() if field in allowed}


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

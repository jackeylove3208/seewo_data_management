"""Load and bind immutable database mappings for execution and rollback."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.source_bindings import AgentSourceBinding, load_source_bindings
from app.agent_runtime.state_machine import AgentPhase
from app.connectors.configured import (
    CANONICAL_DATABASE_MAPPING_FIELDS,
    ConfiguredApiConnector,
    ConnectorCapabilityError,
    DatabaseConnectorConfiguration,
)
from app.connectors.database_runtime import (
    ConfiguredDatabaseConnectorRuntime,
    DatabaseConnectorResolver,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask

DatabaseRole = Literal["authoritative", "target"]
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MAPPING_VERSION = "fixed-six-field-sql-mapping-v3"


@dataclass(frozen=True, slots=True)
class FrozenDatabaseMapping:
    binding: AgentSourceBinding
    configuration: DatabaseConnectorConfiguration
    mapping: dict[str, str]
    schema_fingerprint: str


async def load_frozen_database_mapping(
    session: AsyncSession,
    task_id: UUID,
    run_id: UUID,
    role: DatabaseRole,
) -> dict[str, str]:
    """Return the validated mapping frozen by source ingestion."""

    return dict(
        (
            await load_frozen_database_mapping_context(
                session,
                task_id=task_id,
                run_id=run_id,
                role=role,
            )
        ).mapping
    )


async def load_frozen_database_mapping_context(
    session: AsyncSession,
    *,
    task_id: UUID,
    run_id: UUID,
    role: DatabaseRole,
) -> FrozenDatabaseMapping:
    run = await session.get(AgentRunRecord, run_id)
    task = await session.get(ReconciliationTask, task_id)
    if run is None or task is None or run.task_id != task.id:
        raise ValueError("database mapping run does not belong to the task")

    source_task, source_run = await _mapping_source(session, task=task, run=run)
    authoritative, target = await load_source_bindings(
        session,
        task_id=source_task.id,
        tenant_id=source_task.tenant_id,
    )
    binding = authoritative if role == "authoritative" else target
    if binding.role != role or binding.connector_kind != "database":
        raise ValueError("database mapping source binding does not match the requested role")
    configuration = binding.database_configuration()

    checkpoint = await AgentRuntimeRepository(session).get_checkpoint(
        source_run.id,
        phase=AgentPhase.INGEST_AND_NORMALIZE,
        checkpoint_key=binding.mapping_checkpoint_key,
    )
    if checkpoint is None or checkpoint.status != "completed":
        raise ValueError("frozen database mapping checkpoint is missing")
    payload = checkpoint.payload
    if not isinstance(payload, dict):
        raise ValueError("frozen database mapping checkpoint is malformed")
    if (
        payload.get("schema_version") != "source-ingestion-v3"
        or payload.get("mapping_version") != _MAPPING_VERSION
        or payload.get("source_role") != role
        or payload.get("connector_kind") != "database"
        or payload.get("connector_id") != binding.configuration_id
        or payload.get("resolved") is not True
    ):
        raise ValueError("frozen database mapping checkpoint does not match the source binding")

    mapping_value = payload.get("mapping")
    if not isinstance(mapping_value, dict):
        raise ValueError("frozen database mapping is malformed")
    mapping = {
        str(contract_field): str(column)
        for contract_field, column in mapping_value.items()
        if isinstance(contract_field, str) and isinstance(column, str)
    }
    if (
        len(mapping) != len(mapping_value)
        or frozenset(mapping) != CANONICAL_DATABASE_MAPPING_FIELDS
        or len(set(mapping.values())) != len(mapping)
        or any(not _IDENTIFIER.fullmatch(column) for column in mapping.values())
        or configuration.primary_key in mapping.values()
        or configuration.version_column in mapping.values()
    ):
        raise ValueError("frozen database mapping violates the six-field contract")

    schema_fingerprint = payload.get("schema_fingerprint")
    if (
        not isinstance(schema_fingerprint, str)
        or len(schema_fingerprint) != 71
        or not schema_fingerprint.startswith("sha256:")
        or any(
            character not in "0123456789abcdef"
            for character in schema_fingerprint.removeprefix("sha256:")
        )
    ):
        raise ValueError("frozen database mapping schema fingerprint is malformed")
    return FrozenDatabaseMapping(
        binding=binding,
        configuration=configuration,
        mapping=mapping,
        schema_fingerprint=schema_fingerprint,
    )


async def connector_with_frozen_database_mapping(
    session: AsyncSession,
    *,
    task_id: UUID,
    run_id: UUID,
    role: DatabaseRole,
    connectors: DatabaseConnectorResolver,
    connector_override: ConfiguredApiConnector | None = None,
) -> tuple[str, ConfiguredApiConnector, DatabaseConnectorConfiguration]:
    frozen = await load_frozen_database_mapping_context(
        session,
        task_id=task_id,
        run_id=run_id,
        role=role,
    )
    connector_id = frozen.binding.configuration_id
    if connector_override is not None:
        connector = connector_override
        if connector.configuration != frozen.configuration:
            raise ValueError("database connector override changed the frozen source binding")
    elif isinstance(connectors, ConfiguredDatabaseConnectorRuntime):
        connector = await connectors.connector_for_configuration(
            connector_id,
            frozen.configuration,
        )
    else:
        connector = await connectors.connector(connector_id)
        if connector.configuration != frozen.configuration:
            raise ValueError("database connector configuration changed after task creation")

    schema = await connector.discover_schema()
    current_fingerprint = _schema_fingerprint(
        schema.model_dump(mode="json"),
        frozen.configuration,
    )
    if current_fingerprint != frozen.schema_fingerprint:
        raise ConnectorCapabilityError("database connector schema changed after mapping")
    bound = connector.with_frozen_mapping(frozen.mapping)
    configuration = bound.configuration
    if not isinstance(configuration, DatabaseConnectorConfiguration):
        raise TypeError("frozen database mapping resolved a non-database connector")
    return connector_id, bound, configuration


async def run_requires_frozen_database_mapping(
    session: AsyncSession,
    *,
    task_id: UUID,
    run_id: UUID,
) -> bool:
    run = await session.get(AgentRunRecord, run_id)
    if run is None or run.task_id != task_id:
        raise ValueError("database mapping run does not belong to the task")
    return run.ingestion_contract_version == "source-ingestion-v3"


async def _mapping_source(
    session: AsyncSession,
    *,
    task: ReconciliationTask,
    run: AgentRunRecord,
) -> tuple[ReconciliationTask, AgentRunRecord]:
    if task.task_kind != "rollback":
        return task, run
    source_task_id = task.parent_task_id
    if source_task_id is None and isinstance(task.agent_intent, dict):
        raw_source_task_id = task.agent_intent.get("source_task_id")
        if isinstance(raw_source_task_id, str):
            source_task_id = UUID(raw_source_task_id)
    if source_task_id is None:
        raise ValueError("rollback database mapping source task is missing")
    source_task = await session.get(ReconciliationTask, source_task_id)
    source_run = await session.scalar(
        select(AgentRunRecord)
        .where(
            AgentRunRecord.task_id == source_task_id,
            AgentRunRecord.tenant_id == task.tenant_id,
        )
        .order_by(AgentRunRecord.created_at.desc())
        .limit(1)
    )
    if source_task is None or source_run is None:
        raise ValueError("rollback database mapping source facts are missing")
    if source_run.ingestion_contract_version != "source-ingestion-v3":
        raise ValueError("rollback database mapping source contract changed")
    return source_task, source_run


def _schema_fingerprint(
    schema: dict[str, object],
    configuration: DatabaseConnectorConfiguration,
) -> str:
    value = {
        "schema": schema,
        "table": configuration.table_name,
        "primary_key": configuration.primary_key,
        "version_column": configuration.version_column,
    }
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"

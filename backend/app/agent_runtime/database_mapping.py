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
from app.models.snapshots import SourceFile

DatabaseRole = Literal["authoritative", "target"]
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MAPPING_VERSION = "fixed-six-field-sql-mapping-v3"
_V2_MAPPING_VERSION = "fixed-six-field-sql-mapping-v2"


@dataclass(frozen=True, slots=True)
class FrozenDatabaseMapping:
    binding: AgentSourceBinding
    configuration: DatabaseConnectorConfiguration
    mapping: dict[str, str]
    schema_fingerprint: str


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
    run = await session.get(AgentRunRecord, run_id)
    task = await session.get(ReconciliationTask, task_id)
    if run is None or task is None or run.task_id != task.id:
        raise ValueError("database mapping run does not belong to the task")
    source_task, source_run = await _mapping_source(session, task=task, run=run)
    if source_run.ingestion_contract_version == "source-ingestion-v2":
        return await _connector_with_frozen_v2_database_mapping(
            session,
            source_task=source_task,
            source_run=source_run,
            role=role,
            connectors=connectors,
            connector_override=connector_override,
        )
    if source_run.ingestion_contract_version != "source-ingestion-v3":
        raise ValueError("database mapping source contract is unsupported")
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
    return run.ingestion_contract_version in {
        "source-ingestion-v2",
        "source-ingestion-v3",
    }


async def _connector_with_frozen_v2_database_mapping(
    session: AsyncSession,
    *,
    source_task: ReconciliationTask,
    source_run: AgentRunRecord,
    role: DatabaseRole,
    connectors: DatabaseConnectorResolver,
    connector_override: ConfiguredApiConnector | None,
) -> tuple[str, ConfiguredApiConnector, DatabaseConnectorConfiguration]:
    if not isinstance(source_task.agent_intent, dict):
        raise ValueError("frozen database mapping task intent is missing")
    intent_key = "source" if role == "authoritative" else "target"
    selection = source_task.agent_intent.get(intent_key)
    if not isinstance(selection, dict) or selection.get("kind") != "database":
        raise ValueError("frozen database mapping source selection changed")
    connector_id = selection.get("configuration_id")
    if not isinstance(connector_id, str) or not connector_id:
        raise ValueError("frozen database mapping connector ID is missing")

    connector = connector_override or await connectors.connector(connector_id)
    configuration = connector.configuration
    if not isinstance(configuration, DatabaseConnectorConfiguration):
        raise TypeError("frozen database mapping resolved a non-database connector")
    if configuration.source_role != role:
        raise ValueError("frozen database mapping connector role changed")
    source = await session.scalar(
        select(SourceFile).where(
            SourceFile.task_id == source_task.id,
            SourceFile.source_role == role,
        )
    )
    expected_storage_path = f"database://{connector_id}"
    if (
        source is None
        or source.original_name != connector_id
        or source.storage_path != expected_storage_path
        or source.sha256
        not in _v2_database_configuration_fingerprints(connector_id, configuration)
    ):
        raise ValueError("database connector configuration changed after task creation")

    checkpoint = await AgentRuntimeRepository(session).get_checkpoint(
        source_run.id,
        phase=AgentPhase.INGEST_AND_NORMALIZE,
        checkpoint_key="graph-database-field-mapping-v2",
    )
    if checkpoint is None or checkpoint.status != "completed":
        raise ValueError("frozen database mapping checkpoint is missing")
    payload = checkpoint.payload
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != _V2_MAPPING_VERSION
        or payload.get("resolved") is not True
    ):
        raise ValueError("frozen database mapping checkpoint is malformed")
    mappings = payload.get("mappings")
    mapping_value = mappings.get(role) if isinstance(mappings, dict) else None
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

    schema_fingerprints = payload.get("schema_fingerprints")
    expected_fingerprint = (
        schema_fingerprints.get(role)
        if isinstance(schema_fingerprints, dict)
        else None
    )
    if not isinstance(expected_fingerprint, str):
        raise ValueError("frozen database mapping schema fingerprint is malformed")
    schema = await connector.discover_schema()
    current_fingerprint = _schema_fingerprint(
        schema.model_dump(mode="json"),
        configuration,
    )
    if current_fingerprint != expected_fingerprint:
        raise ConnectorCapabilityError("database connector schema changed after mapping")

    bound = connector.with_frozen_mapping(mapping)
    bound_configuration = bound.configuration
    if not isinstance(bound_configuration, DatabaseConnectorConfiguration):
        raise TypeError("frozen database mapping resolved a non-database connector")
    return connector_id, bound, bound_configuration


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


def _v2_database_configuration_fingerprints(
    connector_id: str,
    configuration: DatabaseConnectorConfiguration,
) -> frozenset[str]:
    legacy = {
        "configuration_id": connector_id,
        "dialect": configuration.dialect,
        "table_name": configuration.table_name,
        "primary_key": configuration.primary_key,
        "version_column": configuration.version_column,
        "field_columns": configuration.field_columns,
        "allowed_columns": configuration.allowed_columns,
        "source_role": configuration.source_role,
    }
    complete = {
        "configuration_id": connector_id,
        "configuration": configuration.model_dump(mode="json"),
    }
    return frozenset({_fact_hash(legacy), _fact_hash(complete)})


def _fact_hash(value: object) -> str:
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"

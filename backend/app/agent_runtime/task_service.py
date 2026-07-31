import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_reporting.rollback_cycles import target_data_source_from_target
from app.agent_runtime.observability import agent_observability
from app.agent_runtime.repository import AgentRuntimeRepository, SchoolLockConflict
from app.agent_runtime.service import AgentSupervisorService
from app.api_connectors.contracts import ProviderManifest
from app.api_connectors.registry import ProviderRegistry
from app.core.config import Settings
from app.core.security import OperatorContext
from app.local_sources.service import LocalSourceService
from app.models.agent_runtime import AgentRunRecord, SchoolTaskLockRecord
from app.models.api_connectors import (
    AgentSourceBindingRecord,
    ApiAuthoritySourceRecord,
    ApiConnectionRecord,
)
from app.models.reconciliation import ReconciliationTask
from app.models.remote_sources import RemoteSourceRecord
from app.models.reporting import AgentReportRecord, AgentRollbackCycleRecord
from app.models.snapshots import Snapshot, SourceFile
from app.remote_sources.repository import RemoteSourceRepository
from app.repositories.files import FileRepository
from app.schemas.agent_api import AgentTaskIntent
from app.schemas.canonical_entities import SourceRole


class AgentTaskConflict(ValueError):
    pass


class AgentTargetBaselineDrift(ValueError):
    def __init__(self, *, source_ref: str) -> None:
        super().__init__(
            "希沃目标文件已在 Agent 之外发生变化；请确认将当前文件作为新基线后再继续"
        )
        self.source_ref = source_ref


class AgentConnectorCapabilityFailure(ValueError):
    pass


class AgentTaskService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        operator: OperatorContext,
        settings: Settings | None = None,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.session = session
        self.operator = operator
        self.settings = settings
        self.provider_registry = provider_registry
        self.runtime = AgentRuntimeRepository(session)

    async def create(
        self,
        intent: AgentTaskIntent,
        *,
        idempotency_key: str,
        conversation_id: UUID | None = None,
        accept_current_target_baseline: bool = False,
    ) -> tuple[ReconciliationTask, AgentRunRecord]:
        self._validate_connector_runtime(intent, conversation_id=conversation_id)
        api_connection: ApiConnectionRecord | None = None
        api_manifest: ProviderManifest | None = None
        if intent.source.kind == "api" and intent.target.kind == "database":
            api_connection, api_manifest = await self._load_api_authority_connection(
                intent,
                conversation_id=conversation_id,
            )
        payload = intent.model_dump(mode="json")
        request_hash = _hash(
            {
                "tenant_id": self.operator.tenant_id,
                "conversation_id": (
                    str(conversation_id) if conversation_id is not None else None
                ),
                **payload,
            }
        )
        existing = await self.session.scalar(
            select(ReconciliationTask).where(ReconciliationTask.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AgentTaskConflict("idempotency key was used for another Agent task")
            run = await self.runtime.get_run_for_task(existing.id)
            if run is None:
                raise AgentTaskConflict("Agent task exists without a runtime")
            return existing, run

        remote_source = await self._load_remote_source(
            intent,
            conversation_id=conversation_id,
        )
        active_lock = await self.session.scalar(
            select(SchoolTaskLockRecord).where(
                SchoolTaskLockRecord.tenant_id == self.operator.tenant_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )
        if active_lock is not None:
            raise SchoolLockConflict(active_lock.owner_task_id)

        accepted_target_baseline_sha256 = await self._check_local_target_baseline(
            intent,
            accept_current=accept_current_target_baseline,
        )
        if accepted_target_baseline_sha256 is not None:
            payload["accepted_target_baseline_sha256"] = (
                accepted_target_baseline_sha256
            )
        workflow_version = (
            self.settings.new_task_workflow_version if self.settings is not None else "new-agent-v1"
        )
        task = ReconciliationTask(
            id=uuid4(),
            tenant_id=self.operator.tenant_id,
            scope_id="all",
            snapshot_mode="full",
            entity_types=sorted(item.value for item in intent.entity_types),
            status="created",
            stage="ingestion",
            workflow_version=workflow_version,
            task_kind="sync",
            title=intent.title.strip(),
            agent_intent=payload,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        self.session.add(task)
        await self.session.flush()
        if intent.source.kind == "csv" and intent.target.kind == "csv":
            assert intent.source.upload_id is not None
            assert intent.target.upload_id is not None
            await self._bind_csv_pair(
                task,
                source_id=intent.source.upload_id,
                target_id=intent.target.upload_id,
            )
        elif intent.source.kind == "local" and intent.target.kind == "local":
            await self._bind_local_pair(
                task,
                source_ref=intent.source.source_ref,
                target_ref=intent.target.source_ref,
            )
        elif intent.source.kind == "csv" and intent.target.kind == "local":
            assert intent.source.upload_id is not None
            await self._bind_uploaded_source_local_target(
                task,
                source_id=intent.source.upload_id,
                target_ref=intent.target.source_ref,
            )
        elif intent.source.kind == "remote_csv" and intent.target.kind == "local":
            assert remote_source is not None
            await self._bind_remote_source_local_target(
                task,
                remote_source=remote_source,
                target_ref=intent.target.source_ref,
            )
        elif intent.source.kind == "database" and intent.target.kind == "database":
            assert intent.source.configuration_id is not None
            assert intent.target.configuration_id is not None
            await self._bind_database_pair(
                task,
                source_configuration_id=intent.source.configuration_id,
                target_configuration_id=intent.target.configuration_id,
            )
        elif intent.source.kind == "api" and intent.target.kind == "database":
            assert api_connection is not None
            assert api_manifest is not None
            assert intent.target.configuration_id is not None
            await self._bind_api_database_pair(
                task,
                connection=api_connection,
                manifest=api_manifest,
                target_configuration_id=intent.target.configuration_id,
            )
        run = await AgentSupervisorService(
            self.session,
            operator=self.operator,
            repository=self.runtime,
            settings=self.settings,
        ).start(task_id=task.id, conversation_id=conversation_id)
        return task, run

    async def _check_local_target_baseline(
        self,
        intent: AgentTaskIntent,
        *,
        accept_current: bool,
    ) -> str | None:
        if (
            self.settings is None
            or intent.target.kind != "local"
            or intent.target.source_ref is None
        ):
            return None
        identity = target_data_source_from_target(
            intent.target.model_dump(mode="json")
        )
        if identity is None:
            return None
        cycle = await self.session.scalar(
            select(AgentRollbackCycleRecord).where(
                AgentRollbackCycleRecord.tenant_id == self.operator.tenant_id,
                AgentRollbackCycleRecord.data_source_key == identity.key,
            )
        )
        if cycle is None or cycle.completed_rollback_task_id is not None:
            return None
        report = await self.session.scalar(
            select(AgentReportRecord).where(
                AgentReportRecord.task_id
                == cycle.latest_successful_sync_task_id,
                AgentReportRecord.tenant_id == self.operator.tenant_id,
            )
        )
        publication = (
            report.facts.get("publication")
            if report is not None and isinstance(report.facts, dict)
            else None
        )
        expected_sha256 = (
            publication.get("published_sha256")
            if isinstance(publication, dict)
            else None
        )
        if not isinstance(expected_sha256, str):
            return None
        material = LocalSourceService(self.settings).describe_target_for_write(
            intent.target.source_ref
        )
        if material.sha256 == expected_sha256:
            return None
        if not accept_current:
            raise AgentTargetBaselineDrift(
                source_ref=material.source_ref,
            )
        return material.sha256

    def _validate_connector_runtime(
        self,
        intent: AgentTaskIntent,
        *,
        conversation_id: UUID | None,
    ) -> None:
        source_kind = intent.source.kind
        target_kind = intent.target.kind
        if source_kind in {"csv", "local"} and target_kind in {"csv", "local"}:
            return
        if source_kind == "remote_csv" and target_kind == "local":
            if conversation_id is None:
                raise AgentConnectorCapabilityFailure(
                    "Remote CSV sources require a conversation"
                )
            if (
                self.settings is None
                or not self.settings.agent_graph_enabled
                or not self.settings.source_ingestion_v2_enabled
            ):
                raise AgentConnectorCapabilityFailure(
                    "Remote CSV graph ingestion is disabled"
                )
            return
        if source_kind == "database" and target_kind == "database":
            self._validate_database_pair(intent)
            return
        if source_kind == "api" and target_kind == "database":
            self._validate_api_database_runtime(intent)
            return
        agent_observability.observe(
            "connector_failed",
            connector_kind=f"{source_kind}+{target_kind}",
            outcome="rejected",
            error_code="unsupported_or_mixed_connector_pair",
        )
        raise AgentConnectorCapabilityFailure(
            "Agent task requires a supported authoritative and target connector pair"
        )

    def _validate_api_database_runtime(self, intent: AgentTaskIntent) -> None:
        if (
            self.settings is None
            or self.provider_registry is None
            or not self.settings.new_agent_api_connector_enabled
            or not self.settings.agent_graph_enabled
            or not self.settings.source_ingestion_v3_enabled
            or not self.settings.agent_graph_sql_execution_enabled
        ):
            raise AgentConnectorCapabilityFailure(
                "API authority graph ingestion is disabled"
            )
        source_id = intent.source.configuration_id
        target_id = intent.target.configuration_id
        if source_id is None or target_id is None:
            raise AgentConnectorCapabilityFailure(
                "API authority and database target are required"
            )
        try:
            UUID(source_id)
        except ValueError as error:
            raise AgentConnectorCapabilityFailure(
                "API connection identifier is invalid"
            ) from error
        target = self.settings.database_connector_configurations.get(target_id)
        if target is None:
            raise AgentConnectorCapabilityFailure(
                "SQL target connector is not configured by the server"
            )
        if target.source_role != "target" or target.dialect != "mysql":
            raise AgentConnectorCapabilityFailure(
                "API task target must be a writable MySQL connector"
            )

    async def _load_api_authority_connection(
        self,
        intent: AgentTaskIntent,
        *,
        conversation_id: UUID | None,
    ) -> tuple[ApiConnectionRecord, ProviderManifest]:
        assert intent.source.configuration_id is not None
        assert self.provider_registry is not None
        connection_id = UUID(intent.source.configuration_id)
        connection = await self.session.scalar(
            select(ApiConnectionRecord).where(
                ApiConnectionRecord.id == connection_id,
                ApiConnectionRecord.tenant_id == self.operator.tenant_id,
            ).with_for_update()
        )
        if connection is None:
            raise AgentConnectorCapabilityFailure(
                "API connection is unavailable for this tenant"
            )
        if conversation_id is not None and (
            connection.scope != "task_ephemeral"
            or connection.conversation_id != conversation_id
        ):
            raise AgentConnectorCapabilityFailure(
                "Conversation API tasks require a task-ephemeral connection"
            )
        if connection.task_id is not None:
            raise AgentConnectorCapabilityFailure(
                "API connection is already bound to another task"
            )
        if connection.credentials_revoked_at is not None:
            raise AgentConnectorCapabilityFailure(
                "API connection credentials are revoked"
            )
        if connection.state != "active":
            raise AgentConnectorCapabilityFailure("API connection is not active")
        last_tested_at = connection.last_tested_at
        if last_tested_at is not None and last_tested_at.tzinfo is None:
            last_tested_at = last_tested_at.replace(tzinfo=UTC)
        if (
            last_tested_at is None
            or self.settings is None
            or last_tested_at
            <= datetime.now(UTC)
            - timedelta(seconds=self.settings.api_connector_test_max_age_seconds)
        ):
            raise AgentConnectorCapabilityFailure(
                "API connection must be tested again before synchronization"
            )
        try:
            manifest = self.provider_registry.manifest(connection.provider_id)
        except KeyError as error:
            raise AgentConnectorCapabilityFailure(
                "API connection provider is unavailable"
            ) from error
        if (
            connection.manifest_version != manifest.manifest_version
            or connection.adapter_version != manifest.adapter_version
        ):
            raise AgentConnectorCapabilityFailure(
                "API connection provider contract is stale"
            )
        selected_entities = tuple(sorted(item.value for item in intent.entity_types))
        supported = {item.value for item in manifest.supported_entities}
        if not set(selected_entities) <= supported:
            raise AgentConnectorCapabilityFailure(
                "API connection does not support the selected entity type"
            )
        if connection.visibility_summary.get("visible") is not True:
            raise AgentConnectorCapabilityFailure("API connection visibility is empty")
        for entity_kind in selected_entities:
            if connection.capabilities.get(f"entity.{entity_kind}.read") is not True:
                raise AgentConnectorCapabilityFailure(
                    "API connection lacks a selected entity capability"
                )
            count = connection.visibility_summary.get(f"{entity_kind}_count")
            if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                raise AgentConnectorCapabilityFailure(
                    "API connection has no visible selected entity records"
                )
        return connection, manifest

    async def _load_remote_source(
        self,
        intent: AgentTaskIntent,
        *,
        conversation_id: UUID | None,
    ) -> RemoteSourceRecord | None:
        if intent.source.kind != "remote_csv":
            return None
        if conversation_id is None or intent.source.remote_source_id is None:
            raise AgentConnectorCapabilityFailure(
                "Remote CSV sources require a conversation binding"
            )
        remote_source = await RemoteSourceRepository(self.session).get_for_conversation(
            intent.source.remote_source_id,
            tenant_id=self.operator.tenant_id,
            created_by=self.operator.operator_id,
            conversation_id=conversation_id,
            for_update=True,
        )
        if remote_source is None:
            raise LookupError("Conversation remote source not found")
        if remote_source.task_id is not None or remote_source.state != "registered":
            raise AgentTaskConflict("Conversation remote source is no longer available")
        return remote_source

    def _validate_database_pair(self, intent: AgentTaskIntent) -> None:
        if (
            self.settings is None
            or not self.settings.source_ingestion_v2_enabled
            or not self.settings.agent_graph_sql_execution_enabled
        ):
            raise AgentConnectorCapabilityFailure("SQL Agent runtime is disabled")
        source_id = intent.source.configuration_id
        target_id = intent.target.configuration_id
        if source_id is None or target_id is None or source_id == target_id:
            raise AgentConnectorCapabilityFailure(
                "SQL Agent task requires two different configured connectors"
            )
        configurations = self.settings.database_connector_configurations
        source = configurations.get(source_id)
        target = configurations.get(target_id)
        if source is None or target is None:
            raise AgentConnectorCapabilityFailure("SQL connector is not configured by the server")
        if source.source_role != "authoritative":
            raise AgentConnectorCapabilityFailure("SQL source connector must be authoritative")
        if target.source_role != "target" or target.dialect != "mysql":
            raise AgentConnectorCapabilityFailure(
                "SQL target connector must be a writable MySQL target"
            )

    async def _bind_database_pair(
        self,
        task: ReconciliationTask,
        *,
        source_configuration_id: str,
        target_configuration_id: str,
    ) -> None:
        if self.settings is None:
            raise ValueError("SQL Agent task requires server connector settings")
        configurations = self.settings.database_connector_configurations
        files: list[SourceFile] = []
        for role, configuration_id in (
            (SourceRole.AUTHORITATIVE, source_configuration_id),
            (SourceRole.TARGET, target_configuration_id),
        ):
            configuration = configurations[configuration_id]
            fingerprint = _hash(
                {
                    "configuration_id": configuration_id,
                    "dialect": configuration.dialect,
                    "table_name": configuration.table_name,
                    "primary_key": configuration.primary_key,
                    "version_column": configuration.version_column,
                    "field_columns": configuration.field_columns,
                    "allowed_columns": configuration.allowed_columns,
                    "source_role": configuration.source_role,
                }
            )
            source = SourceFile(
                id=uuid4(),
                task_id=task.id,
                source_role=role.value,
                original_name=configuration_id,
                storage_name=f"database-{uuid4().hex}",
                storage_path=f"database://{configuration_id}",
                managed_storage=False,
                sha256=fingerprint,
                size_bytes=1,
                detected_encoding=None,
            )
            files.append(source)
        self.session.add_all(files)
        await self.session.flush()
        self.session.add_all(
            (
                _agent_snapshot(task.id, files[0], mapping_version="agent-sql-v2"),
                _agent_snapshot(task.id, files[1], mapping_version="agent-sql-v2"),
            )
        )
        await self.session.flush()

    async def _bind_api_database_pair(
        self,
        task: ReconciliationTask,
        *,
        connection: ApiConnectionRecord,
        manifest: ProviderManifest,
        target_configuration_id: str,
    ) -> None:
        if self.settings is None:
            raise ValueError("API Agent task requires server connector settings")
        if connection.scope == "task_ephemeral":
            connection.task_id = task.id
        target = self.settings.database_connector_configurations[
            target_configuration_id
        ]
        fingerprint = _hash(
            {
                "configuration_id": target_configuration_id,
                "dialect": target.dialect,
                "table_name": target.table_name,
                "primary_key": target.primary_key,
                "version_column": target.version_column,
                "field_columns": target.field_columns,
                "allowed_columns": target.allowed_columns,
                "source_role": target.source_role,
            }
        )
        target_file = SourceFile(
            id=uuid4(),
            task_id=task.id,
            source_role=SourceRole.TARGET.value,
            original_name=target_configuration_id,
            storage_name=f"database-{uuid4().hex}",
            storage_path=f"database://{target_configuration_id}",
            managed_storage=False,
            sha256=fingerprint,
            size_bytes=1,
            detected_encoding=None,
        )
        self.session.add(target_file)
        await self.session.flush()
        target_snapshot = _agent_snapshot(
            task.id,
            target_file,
            mapping_version="agent-sql-v3",
        )
        self.session.add(target_snapshot)
        await self.session.flush()
        selected_entities = sorted(task.entity_types)
        api_source = ApiAuthoritySourceRecord(
            tenant_id=task.tenant_id,
            task_id=task.id,
            connection_id=connection.id,
            frozen_public_configuration=dict(connection.public_configuration),
            frozen_secret_ref=connection.secret_ref,
            selected_entities=selected_entities,
            selection_hash=_hash(selected_entities),
            state="registered",
            manifest_version=manifest.manifest_version,
            adapter_version=manifest.adapter_version,
            projection_version=manifest.projection_version,
        )
        self.session.add(api_source)
        target_configuration = target.model_dump(mode="json")
        self.session.add_all(
            (
                AgentSourceBindingRecord(
                    tenant_id=task.tenant_id,
                    task_id=task.id,
                    role=SourceRole.AUTHORITATIVE.value,
                    connector_kind="api",
                    configuration_id=str(connection.id),
                    snapshot_id=None,
                    configuration_fingerprint=_hash(connection.public_configuration),
                    frozen_public_configuration=dict(connection.public_configuration),
                    credential_reference=connection.secret_ref,
                    mapping_checkpoint_key="graph-api-field-mapping-v3:authoritative",
                    normalization_checkpoint_key=(
                        "graph-source-normalization-v3:authoritative"
                    ),
                ),
                AgentSourceBindingRecord(
                    tenant_id=task.tenant_id,
                    task_id=task.id,
                    role=SourceRole.TARGET.value,
                    connector_kind="database",
                    configuration_id=target_configuration_id,
                    snapshot_id=target_snapshot.id,
                    configuration_fingerprint=_hash(target_configuration),
                    frozen_public_configuration=target_configuration,
                    credential_reference=target.credential_reference,
                    mapping_checkpoint_key="graph-database-field-mapping-v3:target",
                    normalization_checkpoint_key="graph-source-normalization-v3:target",
                ),
            )
        )
        await self.session.flush()

    async def _bind_local_pair(
        self,
        task: ReconciliationTask,
        *,
        source_ref: str | None,
        target_ref: str | None,
    ) -> None:
        if self.settings is None or source_ref is None or target_ref is None:
            raise ValueError("local Agent task requires configured source references")
        sources = LocalSourceService(self.settings)
        source_material = sources.describe(source_ref)
        target_material = sources.describe_target_for_write(target_ref)
        if source_material.path == target_material.path:
            raise ValueError("Agent task requires two different local sources")
        files = FileRepository(self.session)
        source = await files.create(
            source_role=SourceRole.AUTHORITATIVE,
            original_name=source_material.path.name,
            storage_name=f"local-{uuid4().hex}",
            storage_path=source_material.path,
            sha256=source_material.sha256,
            size_bytes=source_material.size_bytes,
            detected_encoding="utf-8",
            managed_storage=False,
        )
        target = await files.create(
            source_role=SourceRole.TARGET,
            original_name=target_material.path.name,
            storage_name=f"local-{uuid4().hex}",
            storage_path=target_material.path,
            sha256=target_material.sha256,
            size_bytes=target_material.size_bytes,
            detected_encoding="utf-8",
            managed_storage=False,
        )
        await self.session.flush()
        await files.bind_to_task(source.id, task.id)
        await files.bind_to_task(target.id, task.id)
        self.session.add_all((_agent_snapshot(task.id, source), _agent_snapshot(task.id, target)))
        await self.session.flush()

    async def _bind_uploaded_source_local_target(
        self,
        task: ReconciliationTask,
        *,
        source_id: UUID,
        target_ref: str | None,
    ) -> None:
        if self.settings is None or target_ref is None:
            raise ValueError("local Agent target requires a configured source reference")
        files = FileRepository(self.session)
        source = await files.get(source_id)
        if source is None:
            raise LookupError("Agent CSV upload not found")
        if source.source_role != SourceRole.AUTHORITATIVE.value:
            raise ValueError("Agent CSV upload role mismatch")
        target_material = LocalSourceService(self.settings).describe_target_for_write(target_ref)
        target = await files.create(
            source_role=SourceRole.TARGET,
            original_name=target_material.path.name,
            storage_name=f"local-{uuid4().hex}",
            storage_path=target_material.path,
            sha256=target_material.sha256,
            size_bytes=target_material.size_bytes,
            detected_encoding="utf-8",
            managed_storage=False,
        )
        await self.session.flush()
        await files.bind_to_task(source.id, task.id)
        await files.bind_to_task(target.id, task.id)
        self.session.add_all((_agent_snapshot(task.id, source), _agent_snapshot(task.id, target)))
        await self.session.flush()

    async def _bind_remote_source_local_target(
        self,
        task: ReconciliationTask,
        *,
        remote_source: RemoteSourceRecord,
        target_ref: str | None,
    ) -> None:
        if self.settings is None or target_ref is None:
            raise ValueError("local Agent target requires a configured source reference")
        target_material = LocalSourceService(self.settings).describe_target_for_write(target_ref)
        files = FileRepository(self.session)
        target = await files.create(
            source_role=SourceRole.TARGET,
            original_name=target_material.path.name,
            storage_name=f"local-{uuid4().hex}",
            storage_path=target_material.path,
            sha256=target_material.sha256,
            size_bytes=target_material.size_bytes,
            detected_encoding="utf-8",
            managed_storage=False,
        )
        await self.session.flush()
        await files.bind_to_task(target.id, task.id)
        await RemoteSourceRepository(self.session).bind_to_task(
            remote_source,
            task_id=task.id,
        )
        self.session.add(_agent_snapshot(task.id, target))
        await self.session.flush()

    async def get(self, task_id: UUID) -> tuple[ReconciliationTask, AgentRunRecord]:
        task = await self.session.scalar(
            select(ReconciliationTask).where(
                ReconciliationTask.id == task_id,
                ReconciliationTask.tenant_id == self.operator.tenant_id,
                ReconciliationTask.workflow_version.in_(("new-agent-v1", "agent-graph-v1")),
            )
        )
        if task is None:
            raise LookupError("Agent task not found")
        run = await self.runtime.get_run_for_task(task.id)
        if run is None:
            raise LookupError("Agent runtime not found")
        return task, run

    async def report_id(self, task_id: UUID) -> UUID | None:
        return cast(
            UUID | None,
            await self.session.scalar(
                select(AgentReportRecord.id).where(AgentReportRecord.task_id == task_id)
            ),
        )

    async def _bind_csv_pair(
        self, task: ReconciliationTask, *, source_id: UUID, target_id: UUID
    ) -> None:
        files = FileRepository(self.session)
        source = await files.get(source_id)
        target = await files.get(target_id)
        if source is None or target is None:
            raise LookupError("Agent CSV upload not found")
        if source.source_role != "authoritative" or target.source_role != "target":
            raise ValueError("Agent CSV upload role mismatch")
        if source.id == target.id:
            raise ValueError("Agent task requires two CSV uploads")
        await files.bind_to_task(source.id, task.id)
        await files.bind_to_task(target.id, task.id)
        self.session.add_all(
            (
                _agent_snapshot(task.id, source),
                _agent_snapshot(task.id, target),
            )
        )
        await self.session.flush()


def _agent_snapshot(
    task_id: UUID,
    source: SourceFile,
    *,
    mapping_version: str = "agent-csv-v1",
) -> Snapshot:
    return Snapshot(
        id=uuid4(),
        task_id=task_id,
        source_file_id=source.id,
        source_role=source.source_role,
        schema_version="agent-contract-v1",
        mapping_version=mapping_version,
        file_hash=source.sha256,
        content_hash=_hash({"source_file_id": str(source.id), "sha256": source.sha256}),
        state="published",
        summary={"total": 0, "accepted": 0, "warnings": 0, "quarantined": 0},
    )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

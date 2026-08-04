"""Production action execution for ``agent-graph-v1`` CSV tasks."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_graph.analysis_executors import (
    GraphAnalysisActionResult,
    GraphAnalysisResultWriter,
    GraphIngestionAnalysisExecutors,
    instantiate_analysis_template,
    partition_analysis_template_work,
)
from app.agent_graph.analysis_tools import GraphAnalysisEvidenceTools
from app.agent_graph.contracts import AllowedActionV1
from app.agent_graph.evidence import (
    EvidenceManifestV1,
    build_evidence_manifest,
    opaque_tenant_ref,
)
from app.agent_graph.governance_executors import (
    FrozenApprovalDraft,
    GraphExecutionTools,
    GraphGovernanceExecutionExecutor,
    GraphHumanGateService,
)
from app.agent_graph.guards import GraphGuardRejected
from app.agent_graph.report_executors import GraphReportExecutor, GraphReportFactTools
from app.agent_graph.repository import AgentGraphRepository, GraphFactConflict
from app.agent_graph.rollback_executors import (
    GraphRollbackAssessmentExecutor,
    GraphRollbackEvidenceTools,
    GraphRollbackExecutionExecutor,
)
from app.agent_graph.tools import GraphPhaseToolGateway
from app.agent_graph.worker import GraphActionOutcome, GraphWorkContext
from app.agent_reporting.rollback_cycles import has_fully_verified_mutations
from app.agent_reporting.service import (
    AgentReportingService,
    rollback_terminal_state,
)
from app.agent_runtime.csv_governance_handlers import (
    CsvGovernanceHandlers,
    build_agent_report_facts,
)
from app.agent_runtime.csv_rollback_handlers import (
    CsvRollbackHandlers,
    _rollback_operations,
)
from app.agent_runtime.local_publication import publish_local_target
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.source_bindings import AgentSourceBinding, load_source_bindings
from app.agent_runtime.sql_governance_handlers import SqlGovernanceExecutionHandler
from app.agent_runtime.sql_rollback_handlers import SqlRollbackExecutionHandler
from app.agent_runtime.state_machine import AgentPhase
from app.agent_runtime.worker import AgentWorkContext
from app.ai.agent_batching import AgentBatchPlanner
from app.ai.graph_subagents import (
    GraphSkillInvocation,
    GraphSkillModelProvider,
    GraphSkillModelRunner,
    GraphSubAgentFailure,
)
from app.ai.skills.contracts import (
    AnalysisTemplateInput,
    AnalysisTemplateOutput,
    CsvColumnProfile,
    CsvFieldMapping,
    CsvSchemaMappingInput,
    CsvSchemaMappingOutput,
    CsvSourceSchemaProfile,
    DatabaseColumnProfile,
    DatabaseFieldMapping,
    DatabaseSchemaMappingInput,
    DatabaseSchemaMappingOutput,
    DatabaseSourceSchemaProfile,
    FailureAnalysisInput,
    FailureAnalysisOutput,
    GovernanceExecutionInput,
    GovernanceReportInput,
    IdentityWorkItem,
    NormalizedOrganizationBatch,
    NormalizeOrganizationBatchInput,
    OperationOutcome,
    ReconcileEntityBatchInput,
    RollbackAssessmentInput,
    RollbackExecutionInput,
    SourceInspectionInput,
    SourceInspectionResult,
)
from app.api_connectors.materializer import ApiAuthorityMaterializer, ApiSourceFailure
from app.connectors.configured import (
    ConfiguredApiConnector,
    ConnectorConflictError,
    DatabaseConnectorConfiguration,
)
from app.connectors.database_runtime import (
    ConfiguredDatabaseConnectorRuntime,
    DatabaseConnectorResolver,
)
from app.core.config import Settings
from app.core.security import OperatorContext
from app.ingestion.agent_api_adapter import AgentApiIngestionAdapter, ApiArtifactBinding
from app.ingestion.agent_contract import AgentContractError, AgentContractMapper
from app.ingestion.agent_csv_adapter import AgentCsvIngestionAdapter
from app.ingestion.agent_database_adapter import AgentDatabaseIngestionAdapter
from app.ingestion.csv_reader import CsvFormatError, inspect_csv, read_csv_frame
from app.models.agent_analysis import (
    AgentApprovalGroupRecord,
    AgentClarificationRecord,
    AgentGovernancePlanRecord,
    AgentInputRecord,
    AgentModelBatchItemRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.models.agent_graph import AgentEvidenceManifestRecord, AgentHumanGateRecord
from app.models.api_connectors import ApiAuthoritySourceRecord, ApiConnectionRecord
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.reconciliation.agent_identity import AgentIdentityIndexBuilder
from app.remote_sources.materializer import RemoteSourceMaterializer
from app.remote_sources.network import RemoteSourceFailure
from app.remote_sources.repository import RemoteSourceRepository
from app.repositories.agent_analysis import AgentAnalysisRepository
from app.repositories.executions import ExecutionRepository
from app.schemas.agent_ingestion import AgentEntityKind, AgentInputMark, AgentSourceRole


@dataclass(frozen=True)
class _DatabaseMappingMaterials:
    profiles: tuple[DatabaseSourceSchemaProfile, ...]
    field_refs: dict[str, dict[str, str]]
    configured_mappings: dict[str, dict[str, str]]
    mapping_modes: dict[str, str]
    connector_ids: dict[str, str]
    schema_fingerprints: dict[str, str]
    source_versions: dict[str, str]
    forbidden_source_refs: dict[str, frozenset[str]]


class ProductionGraphActionExecutor:
    """Execute reviewed graph actions without legacy analysis delegation."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        provider: GraphSkillModelProvider,
        tokenization_secret: str,
        max_retries: int = 3,
        output_root: Path | None = None,
        csv_execution_enabled: bool = False,
        settings: Settings | None = None,
        database_connectors: DatabaseConnectorResolver | None = None,
        remote_materializer: RemoteSourceMaterializer | None = None,
        api_materializer: ApiAuthorityMaterializer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._tokenization_secret = tokenization_secret
        self._max_retries = max_retries
        self._governance = CsvGovernanceHandlers(
            output_root=output_root or Path("storage/exports/agent-targets"),
            settings=settings,
        )
        self._rollback = CsvRollbackHandlers(
            output_root=output_root or Path("storage/exports/agent-targets"),
            settings=settings,
        )
        self._csv_execution_enabled = csv_execution_enabled
        self._settings = settings
        self._analysis_batch_size = settings.analysis_batch_size if settings else 10
        self._remote_materializer = remote_materializer or (
            RemoteSourceMaterializer(settings) if settings is not None else None
        )
        self._api_materializer = api_materializer
        self._database_connectors = database_connectors or (
            ConfiguredDatabaseConnectorRuntime(settings)
            if settings is not None and settings.agent_graph_sql_execution_enabled
            else None
        )
        self._sql_governance = (
            SqlGovernanceExecutionHandler(self._database_connectors)
            if self._database_connectors is not None
            else None
        )
        self._sql_rollback = (
            SqlRollbackExecutionHandler(self._database_connectors)
            if self._database_connectors is not None
            else None
        )

    async def __call__(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        action_kind = action.graph_action_kind or action.action_id
        if action_kind == "materialize_remote_authority":
            if _only(action.resource_ids).startswith("api-source:"):
                return await self._materialize_api_authority(context, action)
            return await self._materialize_remote_authority(context, action)
        if action_kind in {"inspect_authority", "inspect_target"}:
            return await self._inspect_source(context, action)
        if action_kind == "normalize_next_batch":
            return await self._normalize_batch(context, action)
        if action_kind == "build_identity_index":
            return await self._build_identity_index(context, action)
        if action_kind == "construct_identity_work":
            return await self._construct_identity_work(context, action)
        if action_kind in {"analyze_next_batch", "repair_analysis_batch"}:
            return await self._analyze_batch(context, action)
        if action_kind == "resolve_identity_conflicts":
            return await self._open_identity_conflict_gate(context, action)
        if action_kind == "resume_analysis_after_identity_conflicts":
            return await self._resume_analysis_after_identity_conflicts(context, action)
        if action_kind == "enter_aggregate_risk":
            return await self._record_guarded_noop(context, action)
        if action_kind == "aggregate_risk":
            if context.current_node != "aggregate_risk":
                raise GraphGuardRejected("aggregate_risk_action_outside_aggregate_node")
            return await self._aggregate_risk(context, action)
        if action_kind == "compile_execution_plan":
            return await self._compile_execution_plan(context, action)
        if context.current_node == "preflight_execution":
            return await self._preflight_execution(context, action)
        if (
            context.current_node
            in {"execute_ready_operations", "execute_remaining_independent"}
            and action_kind == "verify_operations"
        ):
            return await self._execute_governance(context, action)
        if context.current_node in {
            "generate_terminal_report",
            "abnormal_input_report",
            "termination_report",
        }:
            return await self._generate_report(context, action)
        if context.current_node == "load_verified_mutations":
            return await self._plan_rollback(context, action)
        if context.current_node == "assess_restore_impact":
            return await self._assess_rollback(context, action)
        if (
            context.current_node == "wait_restore_conflicts"
            and action_kind == "wait_rollback_approval"
        ):
            return await self._enter_rollback_approval(context, action)
        if context.current_node == "compile_restore_plan":
            return await self._compile_rollback(context, action)
        if context.current_node == "execute_restore_operations":
            return await self._execute_rollback(context, action)
        if context.current_node == "generate_rollback_report":
            return await self._generate_rollback_report(context, action)
        return await self._record_guarded_noop(context, action)

    async def _materialize_remote_authority(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        if context.graph_version != "agent-sync-graph-v2":
            raise GraphGuardRejected("remote_materialization_requires_sync_graph_v2")
        if context.current_node != "materialize_sources":
            raise GraphGuardRejected("remote_materialization_outside_materialize_node")
        if self._remote_materializer is None:
            raise RuntimeError("Remote source materializer is not configured")
        resource_id = _only(action.resource_ids)
        prefix = "remote-source:"
        if not resource_id.startswith(prefix):
            raise GraphGuardRejected("remote_materialization_resource_invalid")
        try:
            remote_source_id = UUID(resource_id.removeprefix(prefix))
        except ValueError as error:
            raise GraphGuardRejected(
                "remote_materialization_resource_invalid"
            ) from error
        expected_evidence = f"{resource_id}:materialized"
        if action.required_evidence != (expected_evidence,):
            raise GraphGuardRejected("remote_materialization_evidence_invalid")
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    source = await self._remote_materializer.materialize(
                        session,
                        task_id=context.task_id,
                        remote_source_id=remote_source_id,
                    )
                    record = await RemoteSourceRepository(session).get_for_task(
                        tenant_id=context.tenant_id,
                        task_id=context.task_id,
                    )
                    if (
                        record is None
                        or record.id != remote_source_id
                        or record.source_file_id != source.id
                    ):
                        raise GraphGuardRejected(
                            "remote_materialization_binding_changed"
                        )
                    payload = {
                        "resource_id": resource_id,
                        "display_origin": record.display_origin,
                        "source_file_id": str(source.id),
                        "content_sha256": source.sha256,
                        "size_bytes": source.size_bytes,
                        "media_type": record.media_type,
                        "safe_problem_code": record.safe_problem_code,
                    }
                    await AgentRuntimeRepository(session).save_checkpoint(
                        context.run_id,
                        phase=AgentPhase.INGEST_AND_NORMALIZE,
                        checkpoint_key="graph-remote-materialization-v1",
                        input_hash=_hash(
                            {
                                "action": action.action_id,
                                "resource_id": resource_id,
                            }
                        ),
                        payload=payload,
                    )
                    await self._record_deterministic_invocation(
                        session,
                        context=context,
                        action=action,
                        output=payload,
                    )
        except RemoteSourceFailure as error:
            async with self._session_factory() as session:
                async with session.begin():
                    failed = await RemoteSourceRepository(session).get_for_task(
                        tenant_id=context.tenant_id,
                        task_id=context.task_id,
                        for_update=True,
                    )
                    if failed is not None and failed.id == remote_source_id:
                        RemoteSourceRepository.mark_failed(
                            failed,
                            safe_problem_code=error.code,
                        )
            raise
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=(expected_evidence,),
        )

    async def _materialize_api_authority(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        if context.graph_version != "agent-sync-graph-v2":
            raise GraphGuardRejected("api_materialization_requires_sync_graph_v2")
        if context.ingestion_contract_version != "source-ingestion-v3":
            raise GraphGuardRejected("api_materialization_requires_ingestion_v3")
        if context.current_node != "materialize_sources":
            raise GraphGuardRejected("api_materialization_outside_materialize_node")
        if self._api_materializer is None:
            raise RuntimeError("API authority materializer is not configured")
        resource_id = _only(action.resource_ids)
        prefix = "api-source:"
        if not resource_id.startswith(prefix):
            raise GraphGuardRejected("api_materialization_resource_invalid")
        try:
            api_source_id = UUID(resource_id.removeprefix(prefix))
        except ValueError as error:
            raise GraphGuardRejected("api_materialization_resource_invalid") from error
        expected_evidence = f"{resource_id}:materialized"
        if action.required_evidence != (expected_evidence,):
            raise GraphGuardRejected("api_materialization_evidence_invalid")
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    source = await self._api_materializer.materialize(
                        session,
                        task_id=context.task_id,
                        api_source_id=api_source_id,
                    )
                    record = await session.scalar(
                        select(ApiAuthoritySourceRecord).where(
                            ApiAuthoritySourceRecord.id == api_source_id,
                            ApiAuthoritySourceRecord.task_id == context.task_id,
                            ApiAuthoritySourceRecord.tenant_id == context.tenant_id,
                        )
                    )
                    if (
                        record is None
                        or record.state != "ready"
                        or record.source_file_id != source.id
                    ):
                        raise GraphGuardRejected("api_materialization_binding_changed")
                    payload = {
                        "resource_id": resource_id,
                        "source_file_id": str(source.id),
                        "snapshot_id": str(record.snapshot_id),
                        "content_sha256": source.sha256,
                        "size_bytes": source.size_bytes,
                        "record_count": record.record_count,
                        "page_count": record.page_count,
                        "safe_problem_code": record.safe_problem_code,
                    }
                    await AgentRuntimeRepository(session).save_checkpoint(
                        context.run_id,
                        phase=AgentPhase.INGEST_AND_NORMALIZE,
                        checkpoint_key="graph-api-materialization-v1",
                        input_hash=_hash(
                            {
                                "action": action.action_id,
                                "resource_id": resource_id,
                            }
                        ),
                        payload=payload,
                    )
                    await self._record_deterministic_invocation(
                        session,
                        context=context,
                        action=action,
                        output=payload,
                    )
        except ApiSourceFailure as error:
            async with self._session_factory() as session:
                async with session.begin():
                    failed = await session.scalar(
                        select(ApiAuthoritySourceRecord)
                        .where(
                            ApiAuthoritySourceRecord.id == api_source_id,
                            ApiAuthoritySourceRecord.task_id == context.task_id,
                            ApiAuthoritySourceRecord.tenant_id == context.tenant_id,
                        )
                        .with_for_update()
                    )
                    if failed is not None:
                        failed.state = "failed"
                        failed.safe_problem_code = error.code
                        failed.source_file_id = None
                        failed.snapshot_id = None
            raise
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=(expected_evidence,),
        )

    async def _inspect_source(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        if context.ingestion_contract_version == "source-ingestion-v3":
            role = _source_role(_only(action.resource_ids))
            binding = await self._source_binding(
                context.task_id,
                context.tenant_id,
                role,
            )
            if binding.connector_kind == "api":
                return await self._inspect_api_source_v3(context, action, binding)
            if binding.connector_kind == "database":
                return await self._inspect_database_source_v3(
                    context,
                    action,
                    binding,
                )
            if binding.connector_kind == "csv":
                return await self._inspect_csv_source_v2(context, action)
            raise GraphGuardRejected("ingestion_v3_connector_kind_unsupported")
        if context.ingestion_contract_version == "source-ingestion-v2":
            if await self._task_source_mode(context.task_id) == "database":
                return await self._inspect_database_source_v2(context, action)
            return await self._inspect_csv_source_v2(context, action)
        resource_id = _only(action.resource_ids)
        role = _source_role(resource_id)
        async with self._session_factory() as session:
            async with session.begin():
                tools, runner, manifest_id = await self._analysis_runtime(
                    session,
                    context=context,
                    action=action,
                )
                result = await GraphIngestionAnalysisExecutors(runner).inspect_sources(
                    GraphSkillInvocation(
                        task_id=context.task_id,
                        run_id=context.run_id,
                        graph_run_id=context.graph_run_id,
                        graph_node=context.current_node,
                        graph_cursor=context.graph_cursor,
                        action_id=action.action_id,
                        evidence_manifest_id=manifest_id,
                        skill_name="inspect-external-data-source",
                        skill_version="1.0.0",
                        input_payload=SourceInspectionInput(
                            task_id=context.task_id,
                            run_id=context.run_id,
                            phase=AgentPhase.INGEST_AND_NORMALIZE,
                            evidence_refs=action.required_evidence,
                            connector_kind="csv",
                            connector_ref=resource_id,
                        ).model_dump(mode="json"),
                    )
                )
                del tools
                output = result.output
                if not isinstance(output, SourceInspectionResult):
                    raise RuntimeError("validated source inspection changed type")
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=f"graph-source-inspection:{role}",
                    input_hash=_hash(
                        {
                            "action": action.action_id,
                            "resources": action.resource_ids,
                        }
                    ),
                    payload=output.model_dump(mode="json"),
                )
        return _outcome(action)

    async def _inspect_api_source_v3(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
        binding: AgentSourceBinding,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                api_source, connection, _snapshot, source = await _api_source_materials(
                    session,
                    task_id=context.task_id,
                    tenant_id=context.tenant_id,
                    binding=binding,
                    invalid_code="api_authority_materialization_incomplete",
                )
                capabilities = {
                    str(key): value
                    for key, value in connection.capabilities.items()
                    if isinstance(value, bool)
                }
                payload = {
                    "schema_version": "source-ingestion-v3",
                    "mapping_version": api_source.projection_version,
                    "connector_kind": "api",
                    "provider_id": connection.provider_id,
                    "recognized": True,
                    "mapping_required": False,
                    "content_sha256": source.sha256,
                    "record_count": api_source.record_count,
                    "safe_problem_codes": [],
                    "model_calls": 0,
                }
                await AgentAnalysisRepository(session).persist_capability(
                    run_id=context.run_id,
                    task_id=context.task_id,
                    tenant_id=context.tenant_id,
                    source_role=binding.role,
                    connector_kind="api",
                    capabilities=capabilities,
                )
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=f"graph-source-inspection:{binding.role}",
                    input_hash=_hash(
                        {
                            "api_source_id": str(api_source.id),
                            "content_sha256": source.sha256,
                            "projection_version": api_source.projection_version,
                        }
                    ),
                    payload=payload,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output=payload,
                )
        return _outcome(action)

    async def _inspect_database_source_v3(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
        binding: AgentSourceBinding,
    ) -> GraphActionOutcome:
        if binding.connector_kind != "database":
            raise GraphGuardRejected("database_target_binding_invalid")
        return await self._inspect_database_source(
            context,
            action,
            schema_version="source-ingestion-v3",
            mapping_version="fixed-six-field-sql-mapping-v3",
            binding=binding,
        )

    async def _inspect_database_source_v2(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        return await self._inspect_database_source(
            context,
            action,
            schema_version="source-ingestion-v2",
            mapping_version="fixed-six-field-sql-mapping-v2",
        )

    async def _inspect_database_source(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
        *,
        schema_version: str,
        mapping_version: str,
        binding: AgentSourceBinding | None = None,
    ) -> GraphActionOutcome:
        role = _source_role(_only(action.resource_ids))
        if binding is None:
            connector_id = await self._database_connector_id(context.task_id, role)
            connector = await self._database_connector(connector_id)
        else:
            if binding.role != role:
                raise GraphGuardRejected("database_target_binding_changed")
            connector_id = binding.configuration_id
            connector = await self._database_connector_for_binding(binding)
        health = await connector.health()
        schema = await connector.discover_schema()
        version = await connector.version()
        configuration = connector.configuration
        if not isinstance(configuration, DatabaseConnectorConfiguration):
            raise TypeError("SQL task resolved a non-database connector")
        physical_fields = set(schema.fields)
        configured_fields = set(configuration.field_columns.values())
        missing_contract_fields = _fixed_contract_fields().difference(
            configuration.field_columns
        )
        problem_codes: list[str] = []
        if not health.ready:
            problem_codes.append("database_connector_unavailable")
        if not configured_fields <= physical_fields:
            problem_codes.append("database_schema_mapping_stale")
        mapping_required = bool(missing_contract_fields) and not problem_codes
        payload = {
            "schema_version": schema_version,
            "mapping_version": mapping_version,
            "connector_kind": "database",
            "connector_id": connector_id,
            "dialect": configuration.dialect,
            "recognized": not problem_codes and not mapping_required,
            "mapping_required": mapping_required,
            "schema_fingerprint": _hash(
                {
                    "schema": schema.model_dump(mode="json"),
                    "table": configuration.table_name,
                    "primary_key": configuration.primary_key,
                    "version_column": configuration.version_column,
                }
            ),
            "source_version": version.value,
            "safe_problem_codes": problem_codes,
            "model_calls": 0,
        }
        async with self._session_factory() as session:
            async with session.begin():
                await AgentAnalysisRepository(session).persist_capability(
                    run_id=context.run_id,
                    task_id=context.task_id,
                    tenant_id=context.tenant_id,
                    source_role=role,
                    connector_kind="database",
                    capabilities=health.capability_summary.model_dump(mode="json"),
                )
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=f"graph-source-inspection:{role}",
                    input_hash=_hash(
                        {
                            "connector_id": connector_id,
                            "source_version": version.value,
                            "schema_fingerprint": payload["schema_fingerprint"],
                        }
                    ),
                    payload=payload,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output=payload,
                )
        return _outcome(action)

    async def _inspect_csv_source_v2(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        role = _source_role(_only(action.resource_ids))
        async with self._session_factory() as session:
            async with session.begin():
                _snapshot, source = await _source_snapshot(
                    session,
                    task_id=context.task_id,
                    role=role,
                )
                problem_codes: tuple[str, ...] = ()
                headers: tuple[str, ...] = ()
                recognized = True
                mapping_required = False
                try:
                    inspection = inspect_csv(Path(source.storage_path))
                    headers = inspection.headers
                except CsvFormatError as error:
                    recognized = False
                    problem_codes = (_safe_csv_problem_code(error),)
                else:
                    try:
                        AgentContractMapper().assert_recognizable_headers(headers)
                    except AgentContractError as error:
                        recognized = False
                        mapping_required = True
                        problem_codes = (_safe_csv_problem_code(error),)
                await AgentAnalysisRepository(session).persist_capability(
                    run_id=context.run_id,
                    task_id=context.task_id,
                    tenant_id=context.tenant_id,
                    source_role=role,
                    connector_kind="csv",
                    capabilities={
                        "read": True,
                        "write": role == AgentSourceRole.TARGET.value,
                    },
                )
                payload = {
                    "schema_version": "source-ingestion-v2",
                    "mapping_version": "fixed-six-field-mapping-v2",
                    "recognized": recognized,
                    "mapping_required": mapping_required,
                    "detected_fields": list(headers),
                    "safe_problem_codes": list(problem_codes),
                    "model_calls": 0,
                }
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=f"graph-source-inspection:{role}",
                    input_hash=_hash(
                        {
                            "action": action.action_id,
                            "source_hash": source.sha256,
                            "contract": context.ingestion_contract_version,
                        }
                    ),
                    payload=payload,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output=payload,
                )
        return _outcome(action)

    async def _normalize_batch(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        if context.ingestion_contract_version == "source-ingestion-v3":
            resource_id = _only(action.resource_ids)
            role = _source_role(resource_id)
            binding = await self._source_binding(
                context.task_id,
                context.tenant_id,
                role,
            )
            if resource_id.endswith(":mapping"):
                if binding.connector_kind == "api":
                    return await self._resolve_api_mapping_v3(
                        context,
                        action,
                        binding,
                    )
                if binding.connector_kind == "database":
                    return await self._resolve_database_mapping_v3(
                        context,
                        action,
                        binding,
                    )
                if binding.connector_kind == "csv":
                    return await self._resolve_csv_mapping_v3(
                        context,
                        action,
                        binding,
                    )
            elif resource_id.endswith(":full"):
                if binding.connector_kind == "api":
                    return await self._normalize_api_source_v3(
                        context,
                        action,
                        binding,
                    )
                if binding.connector_kind == "database":
                    return await self._normalize_database_source_v3(
                        context,
                        action,
                        binding,
                    )
                if binding.connector_kind == "csv":
                    return await self._normalize_csv_source_v3(
                        context,
                        action,
                        binding,
                    )
            raise GraphGuardRejected("ingestion_v3_resource_invalid")
        if context.ingestion_contract_version == "source-ingestion-v2":
            if action.resource_ids and action.resource_ids[0] == "source-pair:current":
                if await self._task_source_mode(context.task_id) == "database":
                    return await self._resolve_database_mapping_v2(context, action)
                return await self._resolve_csv_mapping_v2(context, action)
            if await self._task_source_mode(context.task_id) == "database":
                return await self._normalize_database_source_v2(context, action)
            return await self._normalize_csv_source_v2(context, action)
        resource_id = _only(action.resource_ids)
        role = _source_role(resource_id)
        page = _source_page(resource_id)
        async with self._session_factory() as session:
            async with session.begin():
                expected_locators = await _page_locators(
                    session,
                    task_id=context.task_id,
                    role=role,
                    page=page,
                )
                if not expected_locators:
                    raise ValueError(
                        "normalization action points to an empty source page"
                    )
                tools, runner, manifest_id = await self._analysis_runtime(
                    session,
                    context=context,
                    action=action,
                )
                result = await GraphIngestionAnalysisExecutors(
                    runner
                ).normalize_input_batch(
                    GraphSkillInvocation(
                        task_id=context.task_id,
                        run_id=context.run_id,
                        graph_run_id=context.graph_run_id,
                        graph_node=context.current_node,
                        graph_cursor=context.graph_cursor,
                        action_id=action.action_id,
                        evidence_manifest_id=manifest_id,
                        skill_name="normalize-organization-data-batch",
                        skill_version="1.0.0",
                        input_payload=NormalizeOrganizationBatchInput(
                            task_id=context.task_id,
                            run_id=context.run_id,
                            phase=AgentPhase.INGEST_AND_NORMALIZE,
                            evidence_refs=action.required_evidence,
                            source_role=role,
                            batch_resource_ids=action.resource_ids,
                            records=(),
                        ).model_dump(mode="json"),
                    ),
                    expected_locators=expected_locators,
                    assert_known_phone_tokens=tools.assert_known_phone_tokens,
                )
                output = result.output
                if not isinstance(output, NormalizedOrganizationBatch):
                    raise RuntimeError("validated normalized output changed type")
                snapshot_id = await _snapshot_id(
                    session,
                    task_id=context.task_id,
                    role=role,
                )
                await GraphAnalysisResultWriter(session).persist_normalized_batch(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    snapshot_id=snapshot_id,
                    tenant_id=context.tenant_id,
                    source_role=AgentSourceRole(role),
                    output=output,
                    resolve_phone_token=tools.resolve_phone_token,
                )
        return _outcome(action)

    async def _resolve_csv_mapping_v3(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
        binding: AgentSourceBinding,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                _snapshot, source = await _source_snapshot(
                    session,
                    task_id=context.task_id,
                    role=binding.role,
                )
                headers = inspect_csv(Path(source.storage_path)).headers
                mapper = AgentContractMapper()
                unresolved: tuple[str, ...] = ()
                try:
                    mapper.assert_recognizable_headers(headers)
                    field_mapping = mapper.resolve_header_mapping(headers)
                    model_calls = 0
                except AgentContractError:
                    profiles, field_refs = _csv_source_profiles(
                        {binding.role: (_snapshot, source)}
                    )
                    _tools, runner, manifest_id = await self._analysis_runtime(
                        session,
                        context=context,
                        action=action,
                    )
                    result = await runner.run(
                        GraphSkillInvocation(
                            task_id=context.task_id,
                            run_id=context.run_id,
                            graph_run_id=context.graph_run_id,
                            graph_node=context.current_node,
                            graph_cursor=context.graph_cursor,
                            action_id=action.action_id,
                            evidence_manifest_id=manifest_id,
                            skill_name="map-csv-organization-schema",
                            skill_version="1.0.0",
                            input_payload=CsvSchemaMappingInput(
                                task_id=context.task_id,
                                run_id=context.run_id,
                                phase=AgentPhase.INGEST_AND_NORMALIZE,
                                evidence_refs=action.required_evidence,
                                sources=profiles,
                            ).model_dump(mode="json"),
                        ),
                        result_validator=lambda output: _validate_csv_mapping_output(
                            output,
                            field_refs=field_refs,
                            roles=(binding.role,),
                        ),
                    )
                    output = result.output
                    if not isinstance(output, CsvSchemaMappingOutput):
                        raise RuntimeError(
                            "validated CSV mapping output changed type"
                        ) from None
                    items = (
                        output.authoritative_mappings
                        if binding.role == "authoritative"
                        else output.target_mappings
                    )
                    unresolved = tuple(
                        item
                        for item in output.unresolved_required_fields
                        if item.startswith(f"{binding.role}.")
                    )
                    field_mapping = {
                        item.contract_field: field_refs[binding.role][
                            item.source_field_ref
                        ]
                        for item in items
                    }
                    model_calls = result.attempt_count
                payload = {
                    "schema_version": "fixed-six-field-csv-mapping-v3",
                    "resolved": not unresolved,
                    "mapping": field_mapping,
                    "source_hash": source.sha256,
                    "model_calls": model_calls,
                }
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=binding.mapping_checkpoint_key,
                    input_hash=_hash(
                        {
                            "role": binding.role,
                            "source_hash": source.sha256,
                            "contract": context.ingestion_contract_version,
                        }
                    ),
                    payload=payload,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output=payload,
                )
        return _outcome(action)

    async def _normalize_csv_source_v3(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
        binding: AgentSourceBinding,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                task = await session.get(ReconciliationTask, context.task_id)
                if task is None:
                    raise LookupError("Agent graph task is missing")
                snapshot, source = await _source_snapshot(
                    session,
                    task_id=context.task_id,
                    role=binding.role,
                )
                mapping = await AgentRuntimeRepository(session).get_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=binding.mapping_checkpoint_key,
                )
                frozen_mapping = (
                    mapping.payload.get("mapping") if mapping is not None else None
                )
                if (
                    mapping is None
                    or not mapping.payload.get("resolved", False)
                    or not isinstance(frozen_mapping, dict)
                    or not all(
                        isinstance(field, str) and isinstance(header, str)
                        for field, header in frozen_mapping.items()
                    )
                ):
                    raise GraphGuardRejected("csv_authority_mapping_unavailable")
                outcome = AgentCsvIngestionAdapter().inspect_csv(
                    path=Path(source.storage_path),
                    task_id=context.task_id,
                    run_id=context.run_id,
                    snapshot_id=snapshot.id,
                    tenant_id=context.tenant_id,
                    source_role=AgentSourceRole(binding.role),
                    selected_entities=_selected_agent_entities(task.entity_types),
                    field_mapping={
                        str(field): str(header)
                        for field, header in frozen_mapping.items()
                    },
                )
                repository = AgentAnalysisRepository(session)
                persisted = await repository.persist_inputs(outcome.records)
                marks = _bind_input_marks(outcome.marks, persisted)
                await repository.persist_marks(marks)
                payload = {
                    "schema_version": "source-ingestion-v3",
                    "mapping_version": "fixed-six-field-csv-mapping-v3",
                    "source_role": binding.role,
                    "connector_kind": "csv",
                    "record_count": len(persisted),
                    "mark_count": len(marks),
                    "model_calls": 0,
                }
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=binding.normalization_checkpoint_key,
                    input_hash=_hash(
                        {
                            "source_hash": source.sha256,
                            "mapping": frozen_mapping,
                            "selected_entities": sorted(task.entity_types),
                        }
                    ),
                    payload=payload,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output=payload,
                )
        return _outcome(action)

    async def _resolve_api_mapping_v3(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
        binding: AgentSourceBinding,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                (
                    api_source,
                    _connection,
                    _snapshot,
                    source,
                ) = await _api_artifact_materials(
                    session,
                    task_id=context.task_id,
                    tenant_id=context.tenant_id,
                    binding=binding,
                )
                payload = {
                    "schema_version": "source-ingestion-v3",
                    "mapping_version": api_source.projection_version,
                    "source_role": binding.role,
                    "connector_kind": "api",
                    "resolved": True,
                    "fixed_fields": sorted(_fixed_contract_fields()),
                    "content_sha256": source.sha256,
                    "model_calls": 0,
                }
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=binding.mapping_checkpoint_key,
                    input_hash=_hash(
                        {
                            "api_source_id": str(api_source.id),
                            "content_sha256": source.sha256,
                            "projection_version": api_source.projection_version,
                        }
                    ),
                    payload=payload,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output=payload,
                )
        return _outcome(action)

    async def _database_mapping_materials(
        self,
        context: GraphWorkContext,
        *,
        bindings_by_role: dict[str, AgentSourceBinding] | None = None,
    ) -> _DatabaseMappingMaterials:
        profiles: dict[str, DatabaseSourceSchemaProfile] = {}
        field_refs: dict[str, dict[str, str]] = {}
        configured_mappings: dict[str, dict[str, str]] = {}
        mapping_modes: dict[str, str] = {}
        connector_ids: dict[str, str] = {}
        schema_fingerprints: dict[str, str] = {}
        source_versions: dict[str, str] = {}
        forbidden_source_refs: dict[str, frozenset[str]] = {}
        roles = (
            ("authoritative", "target")
            if bindings_by_role is None
            else tuple(
                role
                for role in ("authoritative", "target")
                if role in bindings_by_role
            )
        )
        if not roles:
            raise GraphGuardRejected("database_mapping_pair_invalid")
        for role in roles:
            if bindings_by_role is None:
                connector_id = await self._database_connector_id(context.task_id, role)
                connector = await self._database_connector(connector_id)
            else:
                binding = bindings_by_role[role]
                if binding.connector_kind != "database":
                    raise GraphGuardRejected("database_mapping_pair_invalid")
                connector_id = binding.configuration_id
                connector = await self._database_connector_for_binding(binding)
            schema = await connector.discover_schema()
            configuration = connector.configuration
            if not isinstance(configuration, DatabaseConnectorConfiguration):
                raise TypeError("SQL task resolved a non-database connector")
            if configuration.source_role != role:
                raise GraphGuardRejected("database_target_binding_changed")
            missing = set(configuration.field_columns.values()).difference(
                schema.fields
            )
            if missing:
                raise ValueError(
                    f"{role} database mapping references unavailable fields"
                )
            schema_fields = tuple(sorted(schema.fields))
            if (
                configuration.primary_key not in schema_fields
                or configuration.version_column not in schema_fields
            ):
                raise ValueError(
                    f"{role} database schema lacks its key or version column"
                )
            role_refs = {
                f"database-column:{role}:{index}": field
                for index, field in enumerate(schema_fields)
            }
            refs_by_field = {field: field_ref for field_ref, field in role_refs.items()}
            columns_by_name = {column.name: column for column in schema.columns}
            field_refs[role] = role_refs
            configured_mappings[role] = dict(configuration.field_columns)
            mapping_modes[role] = configuration.mapping.mode
            connector_ids[role] = connector_id
            forbidden_source_refs[role] = frozenset(
                {
                    refs_by_field[configuration.primary_key],
                    refs_by_field[configuration.version_column],
                }
            )
            profiles[role] = DatabaseSourceSchemaProfile(
                source_role=role,
                connector_id=connector_id,
                dialect=configuration.dialect,
                relation_ref=(f"database-relation:{role}:{configuration.table_name}"),
                stable_key_ref=refs_by_field[configuration.primary_key],
                version_ref=refs_by_field[configuration.version_column],
                columns=tuple(
                    DatabaseColumnProfile(
                        source_field_ref=field_ref,
                        column_name=field,
                        sql_type=(
                            columns_by_name[field].sql_type
                            if field in columns_by_name
                            else schema.field_types.get(field, "unknown")
                        )
                        or "unknown",
                        inferred_type=_infer_database_column_type(field),
                        nullable=(
                            columns_by_name[field].nullable
                            if field in columns_by_name
                            else field in schema.nullable_fields
                        ),
                        primary_key=(
                            field == configuration.primary_key
                            or (
                                field in columns_by_name
                                and columns_by_name[field].primary_key
                            )
                        ),
                        generated=(
                            columns_by_name[field].generated
                            if field in columns_by_name
                            else False
                        ),
                        autoincrement=(
                            columns_by_name[field].autoincrement
                            if field in columns_by_name
                            else False
                        ),
                        candidate_contract_fields=(
                            ()
                            if field
                            in {
                                configuration.primary_key,
                                configuration.version_column,
                            }
                            else _database_contract_candidates(field)
                        ),
                    )
                    for field_ref, field in role_refs.items()
                ),
            )
            schema_fingerprints[role] = _hash(
                {
                    "schema": schema.model_dump(mode="json"),
                    "table": configuration.table_name,
                    "primary_key": configuration.primary_key,
                    "version_column": configuration.version_column,
                }
            )
            source_versions[role] = (await connector.version()).value
        return _DatabaseMappingMaterials(
            profiles=tuple(profiles[role] for role in roles),
            field_refs=field_refs,
            configured_mappings=configured_mappings,
            mapping_modes=mapping_modes,
            connector_ids=connector_ids,
            schema_fingerprints=schema_fingerprints,
            source_versions=source_versions,
            forbidden_source_refs=forbidden_source_refs,
        )

    async def _resolve_database_mapping_output(
        self,
        session: AsyncSession,
        *,
        context: GraphWorkContext,
        action: AllowedActionV1,
        materials: _DatabaseMappingMaterials,
        mapping_schema_version: str,
        enforce_configured_roles: frozenset[str],
    ) -> tuple[DatabaseSchemaMappingOutput, int, bool, bool]:
        llm_roles = _database_mapping_llm_roles(
            materials,
            mapping_schema_version=mapping_schema_version,
        )
        deterministic = not llm_roles
        if deterministic:
            output = _database_mapping_output_from_config(
                configured_mappings=materials.configured_mappings,
                field_refs=materials.field_refs,
                schema_version=mapping_schema_version,
            )
            return (
                _validate_database_mapping_output(
                    output,
                    field_refs=materials.field_refs,
                    configured_mappings=materials.configured_mappings,
                    enforce_configured_roles=enforce_configured_roles,
                    forbidden_source_refs=materials.forbidden_source_refs,
                    expected_schema_version=mapping_schema_version,
                ),
                0,
                False,
                True,
            )

        runtime = AgentRuntimeRepository(session)
        (
            authoritative_connector_id,
            target_connector_id,
            authoritative_schema_fingerprint,
            target_schema_fingerprint,
        ) = _database_mapping_cache_coordinates(materials)
        cached = await runtime.get_database_schema_mapping(
            tenant_id=context.tenant_id,
            authoritative_connector_id=authoritative_connector_id,
            target_connector_id=target_connector_id,
            authoritative_schema_fingerprint=authoritative_schema_fingerprint,
            target_schema_fingerprint=target_schema_fingerprint,
            ingestion_contract_version=context.ingestion_contract_version,
            skill_name="understand-organization-database-schema",
            skill_version="1.0.0",
        )
        if cached is not None:
            if _hash(cached.mapping) != cached.content_hash:
                raise ValueError("database schema mapping cache failed integrity check")
            output = _validate_database_mapping_output(
                DatabaseSchemaMappingOutput.model_validate(cached.mapping),
                field_refs=materials.field_refs,
                configured_mappings=materials.configured_mappings,
                enforce_configured_roles=enforce_configured_roles,
                forbidden_source_refs=materials.forbidden_source_refs,
                expected_schema_version=mapping_schema_version,
            )
            return output, 0, True, False

        _tools, runner, manifest_id = await self._analysis_runtime(
            session,
            context=context,
            action=action,
            prepare_sensitive_tokens=False,
        )
        model_profiles = tuple(
            profile
            for profile in materials.profiles
            if profile.source_role in llm_roles
        )
        configured_output = _database_mapping_output_from_config(
            configured_mappings=materials.configured_mappings,
            field_refs=materials.field_refs,
            schema_version=mapping_schema_version,
        )

        def validate_model_output(candidate: object) -> DatabaseSchemaMappingOutput:
            output = _merge_database_mapping_roles(
                candidate,
                configured_output=configured_output,
                llm_roles=llm_roles,
            )
            return _validate_database_mapping_output(
                output,
                field_refs=materials.field_refs,
                configured_mappings=materials.configured_mappings,
                enforce_configured_roles=enforce_configured_roles,
                forbidden_source_refs=materials.forbidden_source_refs,
                expected_schema_version=mapping_schema_version,
            )

        result = await GraphIngestionAnalysisExecutors(runner).map_database_schema(
            GraphSkillInvocation(
                task_id=context.task_id,
                run_id=context.run_id,
                graph_run_id=context.graph_run_id,
                graph_node=context.current_node,
                graph_cursor=context.graph_cursor,
                action_id=action.action_id,
                evidence_manifest_id=manifest_id,
                skill_name="understand-organization-database-schema",
                skill_version="1.0.0",
                input_payload=DatabaseSchemaMappingInput(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    evidence_refs=action.required_evidence,
                    mapping_schema_version=mapping_schema_version,
                    sources=model_profiles,
                ).model_dump(mode="json"),
            ),
            result_validator=validate_model_output,
        )
        validated_output = result.output
        if not isinstance(validated_output, DatabaseSchemaMappingOutput):
            raise RuntimeError("validated database mapping output changed type")
        if not validated_output.unresolved_required_fields:
            mapping = validated_output.model_dump(mode="json")
            await runtime.save_database_schema_mapping(
                tenant_id=context.tenant_id,
                authoritative_connector_id=authoritative_connector_id,
                target_connector_id=target_connector_id,
                authoritative_schema_fingerprint=authoritative_schema_fingerprint,
                target_schema_fingerprint=target_schema_fingerprint,
                ingestion_contract_version=context.ingestion_contract_version,
                skill_name="understand-organization-database-schema",
                skill_version="1.0.0",
                mapping=mapping,
                content_hash=_hash(mapping),
            )
        return validated_output, result.attempt_count, False, False

    async def _resolve_database_mapping_v3(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
        binding: AgentSourceBinding,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            database_bindings: dict[str, AgentSourceBinding] = {
                item.role: item
                for item in await load_source_bindings(
                    session,
                    task_id=context.task_id,
                    tenant_id=context.tenant_id,
                )
                if item.connector_kind == "database"
            }
        if set(database_bindings) != {"authoritative", "target"}:
            return await self._resolve_database_mapping_v3_single_role(
                context,
                action,
                binding,
            )

        materials = await self._database_mapping_materials(
            context,
            bindings_by_role=database_bindings,
        )
        enforce_configured_roles = frozenset(
            role for role, mode in materials.mapping_modes.items() if mode == "explicit"
        )
        async with self._session_factory() as session:
            async with session.begin():
                (
                    output,
                    model_calls,
                    cache_hit,
                    deterministic,
                ) = await self._resolve_database_mapping_output(
                    session,
                    context=context,
                    action=action,
                    materials=materials,
                    mapping_schema_version=("fixed-six-field-sql-mapping-v3"),
                    enforce_configured_roles=enforce_configured_roles,
                )
                runtime = AgentRuntimeRepository(session)
                for role in ("authoritative", "target"):
                    role_binding = database_bindings[role]
                    payload = _database_mapping_role_checkpoint_payload(
                        output,
                        role=role,
                        connector_id=materials.connector_ids[role],
                        field_refs=materials.field_refs,
                        schema_fingerprint=materials.schema_fingerprints[role],
                        source_version=materials.source_versions[role],
                        model_calls=model_calls,
                        cache_hit=cache_hit,
                    )
                    await runtime.save_checkpoint(
                        context.run_id,
                        phase=AgentPhase.INGEST_AND_NORMALIZE,
                        checkpoint_key=role_binding.mapping_checkpoint_key,
                        input_hash=_hash(
                            {
                                "role": role,
                                "schema_fingerprints": (materials.schema_fingerprints),
                                "contract": context.ingestion_contract_version,
                            }
                        ),
                        payload=payload,
                    )
                if deterministic:
                    await self._record_deterministic_invocation(
                        session,
                        context=context,
                        action=action,
                        output={
                            role: _database_mapping_role_checkpoint_payload(
                                output,
                                role=role,
                                connector_id=materials.connector_ids[role],
                                field_refs=materials.field_refs,
                                schema_fingerprint=(
                                    materials.schema_fingerprints[role]
                                ),
                                source_version=materials.source_versions[role],
                                model_calls=model_calls,
                                cache_hit=cache_hit,
                            )
                            for role in ("authoritative", "target")
                        },
                    )
        return _outcome(action)

    async def _resolve_database_mapping_v3_single_role(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
        binding: AgentSourceBinding,
    ) -> GraphActionOutcome:
        materials = await self._database_mapping_materials(
            context,
            bindings_by_role={binding.role: binding},
        )
        enforce_configured_roles = frozenset(
            role for role, mode in materials.mapping_modes.items() if mode == "explicit"
        )
        async with self._session_factory() as session:
            async with session.begin():
                (
                    output,
                    model_calls,
                    cache_hit,
                    deterministic,
                ) = await self._resolve_database_mapping_output(
                    session,
                    context=context,
                    action=action,
                    materials=materials,
                    mapping_schema_version="fixed-six-field-sql-mapping-v3",
                    enforce_configured_roles=enforce_configured_roles,
                )
                payload = _database_mapping_role_checkpoint_payload(
                    output,
                    role=binding.role,
                    connector_id=materials.connector_ids[binding.role],
                    field_refs=materials.field_refs,
                    schema_fingerprint=materials.schema_fingerprints[binding.role],
                    source_version=materials.source_versions[binding.role],
                    model_calls=model_calls,
                    cache_hit=cache_hit,
                )
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=binding.mapping_checkpoint_key,
                    input_hash=_hash(
                        {
                            "role": binding.role,
                            "schema_fingerprint": (
                                materials.schema_fingerprints[binding.role]
                            ),
                            "contract": context.ingestion_contract_version,
                        }
                    ),
                    payload=payload,
                )
                if deterministic:
                    await self._record_deterministic_invocation(
                        session,
                        context=context,
                        action=action,
                        output=payload,
                    )
        return _outcome(action)

    async def _normalize_api_source_v3(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
        binding: AgentSourceBinding,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            api_source, connection, snapshot, source = await _api_artifact_materials(
                session,
                task_id=context.task_id,
                tenant_id=context.tenant_id,
                binding=binding,
            )
            mapping = await AgentRuntimeRepository(session).get_checkpoint(
                context.run_id,
                phase=AgentPhase.INGEST_AND_NORMALIZE,
                checkpoint_key=binding.mapping_checkpoint_key,
            )
            if mapping is None or not mapping.payload.get("resolved", False):
                raise GraphGuardRejected("api_authority_mapping_unavailable")
            artifact_binding = ApiArtifactBinding(
                task_id=context.task_id,
                tenant_id=context.tenant_id,
                api_source_id=api_source.id,
                connection_id=connection.id,
                provider_id=connection.provider_id,
                source_file_id=source.id,
                snapshot_id=snapshot.id,
                selection_hash=api_source.selection_hash,
                selected_entities=_selected_agent_entities(
                    api_source.selected_entities
                ),
                manifest_version=api_source.manifest_version,
                adapter_version=api_source.adapter_version,
                projection_version=api_source.projection_version,
                content_sha256=source.sha256,
                size_bytes=source.size_bytes,
            )
        outcome = await AgentApiIngestionAdapter().extract(
            path=Path(source.storage_path),
            run_id=context.run_id,
            binding=artifact_binding,
        )
        async with self._session_factory() as session:
            async with session.begin():
                repository = AgentAnalysisRepository(session)
                persisted = await repository.persist_inputs(outcome.records)
                marks = _bind_api_input_marks(outcome.marks, persisted)
                await repository.persist_marks(marks)
                payload = {
                    "schema_version": "source-ingestion-v3",
                    "mapping_version": api_source.projection_version,
                    "source_role": binding.role,
                    "connector_kind": "api",
                    "content_sha256": source.sha256,
                    "record_count": len(persisted),
                    "mark_count": len(marks),
                    "model_calls": 0,
                }
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=binding.normalization_checkpoint_key,
                    input_hash=_hash(
                        {
                            "api_source_id": str(api_source.id),
                            "content_sha256": source.sha256,
                            "selection_hash": api_source.selection_hash,
                        }
                    ),
                    payload=payload,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output=payload,
                )
        return _outcome(action)

    async def _normalize_database_source_v3(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
        binding: AgentSourceBinding,
    ) -> GraphActionOutcome:
        if binding.configuration_id is None:
            raise GraphGuardRejected("database_target_binding_invalid")
        connector = await self._database_connector_for_binding(binding)
        async with self._session_factory() as session:
            task = await session.get(ReconciliationTask, context.task_id)
            if task is None:
                raise LookupError("Agent graph task is missing")
            mapping = await AgentRuntimeRepository(session).get_checkpoint(
                context.run_id,
                phase=AgentPhase.INGEST_AND_NORMALIZE,
                checkpoint_key=binding.mapping_checkpoint_key,
            )
            if mapping is None or not mapping.payload.get("resolved", False):
                raise GraphGuardRejected("database_target_mapping_unavailable")
            if mapping.payload.get("connector_id") != binding.configuration_id:
                raise GraphGuardRejected("database_target_mapping_invalid")
            frozen_mapping = mapping.payload.get("mapping")
            expected_source_version = mapping.payload.get("source_version")
            if not isinstance(frozen_mapping, dict) or not all(
                isinstance(field, str) and isinstance(column, str)
                for field, column in frozen_mapping.items()
            ):
                raise GraphGuardRejected("database_target_mapping_invalid")
            if set(frozen_mapping) != _fixed_contract_fields() or len(
                set(frozen_mapping.values())
            ) != len(frozen_mapping):
                raise GraphGuardRejected("database_target_mapping_invalid")
            if not isinstance(expected_source_version, str):
                raise GraphGuardRejected("database_target_version_unavailable")
            snapshot, _source = await _source_snapshot(
                session,
                task_id=context.task_id,
                role=binding.role,
            )
        connector = connector.with_frozen_mapping(
            {str(key): str(value) for key, value in frozen_mapping.items()}
        )
        source_version_before = (await connector.version()).value
        if source_version_before != expected_source_version:
            raise ConnectorConflictError(
                "database target changed after its field mapping was frozen"
            )
        outcome = await AgentDatabaseIngestionAdapter().extract(
            connector=connector,
            connector_id=binding.configuration_id,
            task_id=context.task_id,
            run_id=context.run_id,
            snapshot_id=snapshot.id,
            tenant_id=context.tenant_id,
            source_role=AgentSourceRole(binding.role),
            selected_entities=_selected_agent_entities(task.entity_types),
            field_mapping={
                str(key): str(value) for key, value in frozen_mapping.items()
            },
        )
        source_version = (await connector.version()).value
        if source_version != source_version_before:
            raise ConnectorConflictError(
                "database target changed during bounded extraction"
            )
        async with self._session_factory() as session:
            async with session.begin():
                repository = AgentAnalysisRepository(session)
                persisted = await repository.persist_inputs(outcome.records)
                marks = _bind_database_input_marks(outcome.marks, persisted)
                await repository.persist_marks(marks)
                if binding.role == "target":
                    executions = ExecutionRepository(session)
                    current = await executions.current_target_version(
                        context.task_id
                    )
                    if current is None:
                        version_hash = SqlGovernanceExecutionHandler.hash_version(
                            source_version
                        )
                        await executions.create_target_version(
                            task_id=context.task_id,
                            tenant_id=context.tenant_id,
                            source_snapshot_id=snapshot.id,
                            parent_version_id=None,
                            batch_id=None,
                            file_sha256=version_hash,
                            content_hash=_raw_hash(
                                {
                                    "connector_id": binding.configuration_id,
                                    "source_version": source_version,
                                }
                            ),
                            storage_path=(
                                f"database://{binding.configuration_id}/task/"
                                f"{context.task_id}/version/{version_hash}"
                            ),
                        )
                payload = {
                    "schema_version": "source-ingestion-v3",
                    "mapping_version": "fixed-six-field-sql-mapping-v3",
                    "source_role": binding.role,
                    "connector_kind": "database",
                    "connector_id": binding.configuration_id,
                    "source_version": source_version,
                    "record_count": len(persisted),
                    "mark_count": len(marks),
                    "model_calls": 0,
                }
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=binding.normalization_checkpoint_key,
                    input_hash=_hash(
                        {
                            "connector_id": binding.configuration_id,
                            "source_version": source_version,
                            "selected_entities": sorted(task.entity_types),
                        }
                    ),
                    payload=payload,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output=payload,
                )
        return _outcome(action)

    async def _resolve_database_mapping_v2(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        materials = await self._database_mapping_materials(context)
        llm_roles = _database_mapping_llm_roles(
            materials,
            mapping_schema_version="fixed-six-field-sql-mapping-v2",
        )
        async with self._session_factory() as session:
            async with session.begin():
                (
                    output,
                    model_calls,
                    cache_hit,
                    deterministic,
                ) = await self._resolve_database_mapping_output(
                    session,
                    context=context,
                    action=action,
                    materials=materials,
                    mapping_schema_version=("fixed-six-field-sql-mapping-v2"),
                    enforce_configured_roles=(
                        frozenset(materials.field_refs).difference(llm_roles)
                    ),
                )
                payload = _database_mapping_checkpoint_payload(
                    output,
                    field_refs=materials.field_refs,
                    schema_fingerprints=materials.schema_fingerprints,
                    source_versions=materials.source_versions,
                    model_calls=model_calls,
                    cache_hit=cache_hit,
                )
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key="graph-database-field-mapping-v2",
                    input_hash=_hash(
                        {
                            "schema_fingerprints": materials.schema_fingerprints,
                            "contract": context.ingestion_contract_version,
                        }
                    ),
                    payload=payload,
                )
                if deterministic:
                    await self._record_deterministic_invocation(
                        session,
                        context=context,
                        action=action,
                        output=payload,
                    )
        return _outcome(action)

    async def _normalize_database_source_v2(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        role = _source_role(_only(action.resource_ids))
        connector_id = await self._database_connector_id(context.task_id, role)
        connector = await self._database_connector(connector_id)
        async with self._session_factory() as session:
            task = await session.get(ReconciliationTask, context.task_id)
            if task is None:
                raise LookupError("Agent graph task is missing")
            mapping = await AgentRuntimeRepository(session).get_checkpoint(
                context.run_id,
                phase=AgentPhase.INGEST_AND_NORMALIZE,
                checkpoint_key="graph-database-field-mapping-v2",
            )
            if mapping is None or not mapping.payload.get("resolved", False):
                raise ValueError("database ingestion mapping is unavailable")
            role_mappings = mapping.payload.get("mappings")
            if not isinstance(role_mappings, dict):
                raise ValueError("database ingestion mapping is invalid")
            mapped_source_versions = mapping.payload.get("source_versions")
            expected_source_version = (
                mapped_source_versions.get(role)
                if isinstance(mapped_source_versions, dict)
                else None
            )
            if not isinstance(expected_source_version, str):
                raise ValueError("database ingestion source version is unavailable")
            frozen_mapping = role_mappings.get(role)
            if not isinstance(frozen_mapping, dict) or not all(
                isinstance(field, str) and isinstance(column, str)
                for field, column in frozen_mapping.items()
            ):
                raise ValueError("database ingestion role mapping is invalid")
            if set(frozen_mapping) != _fixed_contract_fields() or len(
                set(frozen_mapping.values())
            ) != len(frozen_mapping):
                raise ValueError("database ingestion role mapping is invalid")
            snapshot, _source = await _source_snapshot(
                session,
                task_id=context.task_id,
                role=role,
            )
        connector = connector.with_frozen_mapping(
            {str(key): str(value) for key, value in frozen_mapping.items()}
        )
        source_version_before = (await connector.version()).value
        if source_version_before != expected_source_version:
            raise ConnectorConflictError(
                "database source changed after its field mapping was frozen"
            )
        outcome = await AgentDatabaseIngestionAdapter().extract(
            connector=connector,
            connector_id=connector_id,
            task_id=context.task_id,
            run_id=context.run_id,
            snapshot_id=snapshot.id,
            tenant_id=context.tenant_id,
            source_role=AgentSourceRole(role),
            selected_entities=_selected_agent_entities(task.entity_types),
            field_mapping=frozen_mapping,
        )
        source_version = (await connector.version()).value
        if source_version != source_version_before:
            raise ConnectorConflictError(
                "database source changed during bounded extraction"
            )
        async with self._session_factory() as session:
            async with session.begin():
                repository = AgentAnalysisRepository(session)
                persisted = await repository.persist_inputs(outcome.records)
                marks = _bind_database_input_marks(outcome.marks, persisted)
                await repository.persist_marks(marks)
                if role == "target":
                    executions = ExecutionRepository(session)
                    current = await executions.current_target_version(context.task_id)
                    if current is None:
                        version_hash = SqlGovernanceExecutionHandler.hash_version(
                            source_version
                        )
                        await executions.create_target_version(
                            task_id=context.task_id,
                            tenant_id=context.tenant_id,
                            source_snapshot_id=snapshot.id,
                            parent_version_id=None,
                            batch_id=None,
                            file_sha256=version_hash,
                            content_hash=_raw_hash(
                                {
                                    "connector_id": connector_id,
                                    "source_version": source_version,
                                }
                            ),
                            storage_path=(
                                f"database://{connector_id}/task/"
                                f"{context.task_id}/version/{version_hash}"
                            ),
                        )
                payload = {
                    "schema_version": "source-ingestion-v2",
                    "mapping_version": "fixed-six-field-sql-mapping-v2",
                    "source_role": role,
                    "connector_id": connector_id,
                    "source_version": source_version,
                    "record_count": len(persisted),
                    "mark_count": len(marks),
                    "model_calls": 0,
                }
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=f"graph-source-normalization:{role}",
                    input_hash=_hash(
                        {
                            "connector_id": connector_id,
                            "source_version": payload["source_version"],
                            "selected_entities": sorted(task.entity_types),
                        }
                    ),
                    payload=payload,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output=payload,
                )
        return _outcome(action)

    async def _task_source_mode(self, task_id: UUID) -> str:
        async with self._session_factory() as session:
            task = await session.get(ReconciliationTask, task_id)
            if task is None or not isinstance(task.agent_intent, dict):
                return "csv"
            source = task.agent_intent.get("source")
            return (
                "database"
                if isinstance(source, dict) and source.get("kind") == "database"
                else "csv"
            )

    async def _source_binding(
        self,
        task_id: UUID,
        tenant_id: str,
        role: str,
    ) -> AgentSourceBinding:
        async with self._session_factory() as session:
            for binding in await load_source_bindings(
                session,
                task_id=task_id,
                tenant_id=tenant_id,
            ):
                if binding.role == role:
                    return binding
        raise GraphGuardRejected("source_role_binding_invalid")

    async def _database_connector_id(self, task_id: UUID, role: str) -> str:
        async with self._session_factory() as session:
            task = await session.get(ReconciliationTask, task_id)
            if task is None or not isinstance(task.agent_intent, dict):
                raise LookupError("SQL Agent task intent is missing")
            selection = task.agent_intent.get(
                "source" if role == "authoritative" else "target"
            )
            if not isinstance(selection, dict) or selection.get("kind") != "database":
                raise ValueError("SQL Agent task source pair changed")
            connector_id = selection.get("configuration_id")
            if not isinstance(connector_id, str) or not connector_id:
                raise ValueError("SQL Agent connector ID is missing")
            return connector_id

    async def _database_connector(
        self,
        connector_id: str,
    ) -> ConfiguredApiConnector:
        if self._database_connectors is None:
            raise RuntimeError("SQL Agent connector runtime is unavailable")
        return await self._database_connectors.connector(connector_id)

    async def _database_connector_for_binding(
        self,
        binding: AgentSourceBinding,
    ) -> ConfiguredApiConnector:
        if self._database_connectors is None:
            raise RuntimeError("SQL Agent connector runtime is unavailable")
        configuration = binding.database_configuration()
        if isinstance(self._database_connectors, ConfiguredDatabaseConnectorRuntime):
            return await self._database_connectors.connector_for_configuration(
                binding.configuration_id,
                configuration,
            )
        connector = await self._database_connectors.connector(binding.configuration_id)
        if connector.configuration != configuration:
            raise GraphGuardRejected("database_target_binding_changed")
        return connector

    async def _resolve_csv_mapping_v2(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                materials = {
                    role: await _source_snapshot(
                        session,
                        task_id=context.task_id,
                        role=role,
                    )
                    for role in ("authoritative", "target")
                }
                profiles, field_refs = _csv_source_profiles(materials)
                deterministic = _deterministic_csv_pair_mapping(profiles)
                if deterministic is not None:
                    payload = _csv_mapping_checkpoint_payload(
                        deterministic,
                        field_refs=field_refs,
                        model_calls=0,
                    )
                    await AgentRuntimeRepository(session).save_checkpoint(
                        context.run_id,
                        phase=AgentPhase.INGEST_AND_NORMALIZE,
                        checkpoint_key="graph-csv-field-mapping-v2",
                        input_hash=_csv_mapping_input_hash(materials),
                        payload=payload,
                    )
                    await self._record_deterministic_invocation(
                        session,
                        context=context,
                        action=action,
                        output=payload,
                    )
                    return _outcome(action)

                _tools, runner, manifest_id = await self._analysis_runtime(
                    session,
                    context=context,
                    action=action,
                )
                task = await session.get(ReconciliationTask, context.task_id)
                skill_name = (
                    "understand-remote-organization-source"
                    if _task_uses_remote_csv(task)
                    else "map-csv-organization-schema"
                )
                result = await runner.run(
                    GraphSkillInvocation(
                        task_id=context.task_id,
                        run_id=context.run_id,
                        graph_run_id=context.graph_run_id,
                        graph_node=context.current_node,
                        graph_cursor=context.graph_cursor,
                        action_id=action.action_id,
                        evidence_manifest_id=manifest_id,
                        skill_name=skill_name,
                        skill_version="1.0.0",
                        input_payload=CsvSchemaMappingInput(
                            task_id=context.task_id,
                            run_id=context.run_id,
                            phase=AgentPhase.INGEST_AND_NORMALIZE,
                            evidence_refs=action.required_evidence,
                            sources=profiles,
                        ).model_dump(mode="json"),
                    ),
                    result_validator=lambda output: _validate_csv_mapping_output(
                        output,
                        field_refs=field_refs,
                    ),
                )
                output = result.output
                if not isinstance(output, CsvSchemaMappingOutput):
                    raise RuntimeError("validated CSV mapping output changed type")
                payload = _csv_mapping_checkpoint_payload(
                    output,
                    field_refs=field_refs,
                    model_calls=result.attempt_count,
                )
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key="graph-csv-field-mapping-v2",
                    input_hash=_csv_mapping_input_hash(materials),
                    payload=payload,
                )
        return _outcome(action)

    async def _normalize_csv_source_v2(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        role = _source_role(_only(action.resource_ids))
        async with self._session_factory() as session:
            async with session.begin():
                task = await session.get(ReconciliationTask, context.task_id)
                if task is None:
                    raise LookupError("Agent graph task is missing")
                snapshot, source = await _source_snapshot(
                    session,
                    task_id=context.task_id,
                    role=role,
                )
                mapping_checkpoint = await AgentRuntimeRepository(
                    session
                ).get_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key="graph-csv-field-mapping-v2",
                )
                if mapping_checkpoint is None:
                    field_mapping = AgentContractMapper().resolve_header_mapping(
                        inspect_csv(Path(source.storage_path)).headers
                    )
                else:
                    if not mapping_checkpoint.payload.get("resolved", False):
                        raise ValueError("CSV fixed-field mapping is unresolved")
                    mappings = mapping_checkpoint.payload.get("mappings", {})
                    role_mapping = (
                        mappings.get(role) if isinstance(mappings, dict) else None
                    )
                    if not isinstance(role_mapping, dict):
                        raise ValueError(
                            "CSV fixed-field mapping is missing a source role"
                        )
                    field_mapping = {
                        str(contract_field): str(header)
                        for contract_field, header in role_mapping.items()
                    }
                outcome = AgentCsvIngestionAdapter().inspect_csv(
                    path=Path(source.storage_path),
                    task_id=context.task_id,
                    run_id=context.run_id,
                    snapshot_id=snapshot.id,
                    tenant_id=context.tenant_id,
                    source_role=AgentSourceRole(role),
                    selected_entities=_selected_agent_entities(task.entity_types),
                    field_mapping=field_mapping,
                )
                repository = AgentAnalysisRepository(session)
                persisted = await repository.persist_inputs(outcome.records)
                marks = _bind_input_marks(outcome.marks, persisted)
                await repository.persist_marks(marks)
                payload = {
                    "schema_version": "source-ingestion-v2",
                    "mapping_version": "fixed-six-field-mapping-v2",
                    "source_role": role,
                    "record_count": len(persisted),
                    "mark_count": len(marks),
                    "model_calls": 0,
                }
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=f"graph-source-normalization:{role}",
                    input_hash=_hash(
                        {
                            "source_hash": source.sha256,
                            "mapping_version": "fixed-six-field-mapping-v2",
                            "selected_entities": sorted(task.entity_types),
                        }
                    ),
                    payload=payload,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output=payload,
                )
        return _outcome(action)

    async def _build_identity_index(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                await AgentIdentityIndexBuilder(session).build(run_id=context.run_id)
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output={"identity_index": "persisted"},
                )
        return _outcome(action)

    async def _construct_identity_work(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                batches = await AgentBatchPlanner(
                    session,
                    max_items=self._analysis_batch_size,
                    group_by_work_kind=True,
                ).create_for_run(
                    run_id=context.run_id
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output={
                        "analysis_batch_ids": [str(batch.id) for batch in batches],
                    },
                )
        return _outcome(action)

    async def _analyze_batch(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        work_ids = tuple(
            _resource_uuid(value, "work-item") for value in action.resource_ids
        )
        async with self._session_factory() as preparation_session:
            async with preparation_session.begin():
                batch = await _find_exact_batch(
                    preparation_session,
                    run_id=context.run_id,
                    work_ids=work_ids,
                )
                repository = AgentAnalysisRepository(preparation_session)
                claimed = await repository.claim_batch(
                    batch.id,
                    worker_id=context.worker_id,
                    run_lease_token=context.lease_token,
                    lease_seconds=60,
                )
                if claimed is None or claimed.lease_token is None:
                    raise RuntimeError("analysis batch is not claimable")
                batch_id = claimed.id
                batch_lease_token = claimed.lease_token
                work_rows = await _work_rows(preparation_session, work_ids)
                expected_kinds = {work.id: work.kind for work, _record in work_rows}
                tools, _runner, manifest_id = await self._analysis_runtime(
                    preparation_session,
                    context=context,
                    action=action,
                )
                paired_evidence = {
                    work.id: await tools.paired_record_evidence(
                        f"paired-record:{work.id}"
                    )
                    for work, _record in work_rows
                }
                identity_work_items = tuple(
                    IdentityWorkItem(
                        work_item_id=work.id,
                        entity_kind=record.entity_kind,
                        target_locator=record.stable_locator,
                        candidate_evidence_refs=(f"paired-record:{work.id}",),
                        paired_evidence=paired_evidence[work.id],
                    )
                    for work, record in work_rows
                )
                template_context, fallback_work_items = partition_analysis_template_work(
                    identity_work_items
                )
                fallback_action = (
                    _fallback_analysis_action(
                        action,
                        tuple(item.work_item_id for item in fallback_work_items),
                    )
                    if template_context is not None and fallback_work_items
                    else None
                )
                input_payload = ReconcileEntityBatchInput(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    phase=AgentPhase.ANALYZE_BATCHES,
                    evidence_refs=(
                        fallback_action.required_evidence
                        if fallback_action is not None
                        else action.required_evidence
                    ),
                    work_items=(
                        fallback_work_items
                        if template_context is not None
                        else identity_work_items
                    ),
                ).model_dump(mode="json")

        result = None
        model_failure: GraphSubAgentFailure | None = None
        async with self._session_factory() as model_session:
            tools, runner, replay_manifest_id = await self._analysis_runtime(
                model_session,
                context=context,
                action=action,
                durable_tool_recovery=True,
            )
            if replay_manifest_id != manifest_id:
                raise RuntimeError(
                    "analysis evidence manifest replay changed identity"
                )
            try:
                executors = GraphIngestionAnalysisExecutors(runner)
                invocation = GraphSkillInvocation(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    graph_run_id=context.graph_run_id,
                    graph_node=context.current_node,
                    graph_cursor=context.graph_cursor,
                    action_id=action.action_id,
                    evidence_manifest_id=manifest_id,
                    skill_name="reconcile-entity-batch",
                    skill_version="1.0.0",
                    input_payload=input_payload,
                )
                if template_context is None:
                    result = await executors.analyze_actionable_batch(
                        invocation,
                        expected_work_item_kinds=expected_kinds,
                        allowed_evidence_refs=frozenset(action.required_evidence),
                    )
                else:
                    runtime_repository = AgentRuntimeRepository(model_session)
                    checkpoint_key = (
                        "agent-analysis-template-v1:"
                        f"{template_context.profile_hash.removeprefix('sha256:')}"
                    )
                    checkpoint = await runtime_repository.get_checkpoint(
                        context.run_id,
                        phase=AgentPhase.ANALYZE_BATCHES,
                        checkpoint_key=checkpoint_key,
                    )
                    if checkpoint is not None:
                        template = AnalysisTemplateOutput.model_validate(
                            checkpoint.payload["template"]
                        )
                        invocation_id = UUID(
                            str(checkpoint.payload["model_invocation_id"])
                        )
                        result = GraphAnalysisActionResult(
                            payloads=instantiate_analysis_template(
                                template_context,
                                template,
                                allowed_evidence_refs=frozenset(
                                    action.required_evidence
                                ),
                            ),
                            reconciliation_invocation_id=invocation_id,
                            solution_invocation_id=invocation_id,
                        )
                        await runtime_repository.append_event(
                            context.run_id,
                            "analysis.template.reused",
                            {
                                "profile_hash": template_context.profile_hash,
                                "batch_id": str(batch_id),
                                "item_count": len(template_context.work_items),
                            },
                        )
                        await model_session.commit()
                    else:
                        template_result = await executors.derive_actionable_template(
                            invocation.model_copy(
                                update={
                                    "input_payload": AnalysisTemplateInput(
                                        task_id=context.task_id,
                                        run_id=context.run_id,
                                        phase=AgentPhase.ANALYZE_BATCHES,
                                        evidence_refs=action.required_evidence,
                                        profile_hash=template_context.profile_hash,
                                        profile=template_context.profile,
                                        representative=template_context.work_items[0],
                                    ).model_dump(mode="json")
                                }
                            ),
                            template_context=template_context,
                            allowed_evidence_refs=frozenset(
                                action.required_evidence
                            ),
                        )
                        result = template_result.action_result
                        await runtime_repository.save_checkpoint(
                            context.run_id,
                            phase=AgentPhase.ANALYZE_BATCHES,
                            checkpoint_key=checkpoint_key,
                            input_hash=template_context.profile_hash,
                            payload={
                                "schema_version": (
                                    "analysis-template-checkpoint-v1"
                                ),
                                "profile_hash": template_context.profile_hash,
                                "profile": template_context.profile.model_dump(
                                    mode="json"
                                ),
                                "template": template_result.template.model_dump(
                                    mode="json"
                                ),
                                "model_invocation_id": str(
                                    result.reconciliation_invocation_id
                                ),
                            },
                        )
                        await runtime_repository.append_event(
                            context.run_id,
                            "analysis.template.created",
                            {
                                "profile_hash": template_context.profile_hash,
                                "batch_id": str(batch_id),
                                "item_count": len(template_context.work_items),
                            },
                        )
                        await model_session.commit()
                    if fallback_work_items:
                        if fallback_action is None:
                            raise RuntimeError(
                                "analysis fallback action was not prepared"
                            )
                        fallback_kinds = {
                            item.work_item_id: expected_kinds[item.work_item_id]
                            for item in fallback_work_items
                        }
                        (
                            fallback_tools,
                            fallback_runner,
                            fallback_manifest_id,
                        ) = await self._analysis_runtime(
                            model_session,
                            context=context,
                            action=fallback_action,
                            durable_tool_recovery=True,
                        )
                        fallback_invocation = invocation.model_copy(
                            update={
                                "action_id": fallback_action.action_id,
                                "evidence_manifest_id": fallback_manifest_id,
                            }
                        )
                        fallback_result = await GraphIngestionAnalysisExecutors(
                            fallback_runner
                        ).analyze_actionable_batch(
                            fallback_invocation,
                            expected_work_item_kinds=fallback_kinds,
                            allowed_evidence_refs=frozenset(
                                fallback_action.required_evidence
                            ),
                        )
                        del fallback_tools
                        if result is None:
                            result = fallback_result
                        else:
                            result = GraphAnalysisActionResult(
                                payloads=result.payloads + fallback_result.payloads,
                                reconciliation_invocation_id=(
                                    fallback_result.reconciliation_invocation_id
                                ),
                                solution_invocation_id=(
                                    fallback_result.solution_invocation_id
                                ),
                            )
            except GraphSubAgentFailure as error:
                model_failure = error
            del tools

        if model_failure is not None:
            async with self._session_factory() as release_session:
                async with release_session.begin():
                    await AgentAnalysisRepository(release_session).release_batch_claim(
                        batch_id=batch_id,
                        worker_id=context.worker_id,
                        lease_token=batch_lease_token,
                    )
            raise model_failure
        if result is None:
            raise RuntimeError("analysis model completed without a validated result")

        async with self._session_factory() as result_session:
            async with result_session.begin():
                await AgentAnalysisRepository(result_session).finalize_batch(
                    batch_id=batch_id,
                    worker_id=context.worker_id,
                    run_lease_token=context.lease_token,
                    lease_token=batch_lease_token,
                    output_hash="validated-graph-output",
                    findings=result.payloads,
                )
        return _outcome(action)

    async def _open_identity_conflict_gate(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                clarifications = tuple(
                    await session.scalars(
                        select(AgentClarificationRecord)
                        .where(
                            AgentClarificationRecord.run_id == context.run_id,
                            AgentClarificationRecord.status.in_(
                                ("pending", "interpreted")
                            ),
                        )
                        .order_by(
                            AgentClarificationRecord.created_at,
                            AgentClarificationRecord.id,
                        )
                    )
                )
                if not clarifications:
                    raise ValueError(
                        "identity conflict action has no unresolved clarification"
                    )
                await AgentGraphRepository(session).record_human_gate(
                    graph_run_id=context.graph_run_id,
                    cursor=context.graph_cursor,
                    gate_kind="identity_conflict",
                    member_ids=tuple(str(item.id) for item in clarifications),
                    content_hash=_hash(
                        [
                            {
                                "id": str(item.id),
                                "work_item_id": str(item.work_item_id),
                                "masked_candidates": item.masked_candidates,
                                "allowed_outcomes": item.allowed_outcomes,
                            }
                            for item in clarifications
                        ]
                    ),
                    status="pending",
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output={
                        "clarification_ids": [str(item.id) for item in clarifications],
                        "interaction": "operator_dialogue_required",
                    },
                )
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=action.required_evidence,
            pause_for_human=True,
        )

    async def _resume_analysis_after_identity_conflicts(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                resolved = await AgentIdentityIndexBuilder(
                    session
                ).resolve_confirmed_conflicts(run_id=context.run_id)
                batches = await AgentBatchPlanner(
                    session,
                    max_items=self._analysis_batch_size,
                    group_by_work_kind=True,
                ).create_for_run(
                    run_id=context.run_id,
                    work_item_ids=tuple(item.id for item in resolved),
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output={
                        "resolved_work_item_ids": [str(item.id) for item in resolved],
                        "analysis_batch_ids": [str(batch.id) for batch in batches],
                    },
                )
        return _outcome(action)

    async def _aggregate_risk(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                result = await self._governance.aggregate(
                    session,
                    _legacy_context(
                        context,
                        AgentPhase.AGGREGATE_RISK_AND_APPROVALS,
                    ),
                )
                groups = tuple(
                    await session.scalars(
                        select(AgentApprovalGroupRecord)
                        .where(AgentApprovalGroupRecord.run_id == context.run_id)
                        .order_by(AgentApprovalGroupRecord.id)
                    )
                )
                drafts = tuple(
                    FrozenApprovalDraft(
                        group_key=group.group_key,
                        finding_ids=tuple(UUID(item) for item in group.finding_ids),
                        issue_kind=group.issue_kind,
                        entity_kind=group.entity_kind,
                        operation=group.operation,
                        risk=group.risk,
                        policy_version=group.policy_version,
                    )
                    for group in groups
                    if group.status == "pending"
                )
                await GraphHumanGateService(session).freeze_high_risk_approvals(
                    graph_run_id=context.graph_run_id,
                    cursor=context.graph_cursor,
                    groups=drafts,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output={
                        "approval_group_count": len(groups),
                        "pending_group_count": len(drafts),
                    },
                )
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=action.required_evidence,
            pause_for_human=result.next_status is not None,
        )

    async def _compile_execution_plan(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                await self._governance.compile(
                    session,
                    _legacy_context(
                        context,
                        AgentPhase.COMPILE_EXECUTION_PLAN,
                    ),
                )
                plan = await session.scalar(
                    select(AgentGovernancePlanRecord)
                    .where(AgentGovernancePlanRecord.run_id == context.run_id)
                    .order_by(AgentGovernancePlanRecord.created_at.desc())
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output={
                        "plan_id": str(plan.id) if plan is not None else None,
                        "operation_count": len(plan.operations)
                        if plan is not None
                        else 0,
                    },
                )
        return _outcome(action)

    async def _preflight_execution(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        action_kind = action.graph_action_kind or action.action_id
        async with self._session_factory() as session:
            async with session.begin():
                plan = await session.scalar(
                    select(AgentGovernancePlanRecord)
                    .where(AgentGovernancePlanRecord.run_id == context.run_id)
                    .order_by(AgentGovernancePlanRecord.created_at.desc())
                )
                current = await ExecutionRepository(session).current_target_version(
                    context.task_id
                )
                task = await session.get(ReconciliationTask, context.task_id)
                external_version_hash: str | None = None
                if _task_uses_database(task):
                    if task is None or not isinstance(task.agent_intent, dict):
                        raise LookupError("SQL Agent task intent is missing")
                    target = task.agent_intent.get("target")
                    connector_id = (
                        target.get("configuration_id")
                        if isinstance(target, dict)
                        else None
                    )
                    if not isinstance(connector_id, str):
                        raise ValueError("SQL Agent target connector ID is missing")
                    if context.ingestion_contract_version == "source-ingestion-v3":
                        target_binding = next(
                            binding
                            for binding in await load_source_bindings(
                                session,
                                task_id=context.task_id,
                                tenant_id=context.tenant_id,
                            )
                            if binding.role == "target"
                        )
                        connector = await self._database_connector_for_binding(
                            target_binding
                        )
                    else:
                        connector = await self._database_connector(connector_id)
                    external_version_hash = SqlGovernanceExecutionHandler.hash_version(
                        (await connector.version()).value
                    )
                stale = plan is not None and (
                    current is None
                    or f"sha256:{current.file_sha256}" != plan.target_version
                    or (
                        external_version_hash is not None
                        and current.file_sha256 != external_version_hash
                    )
                )
                expected = (
                    "request_cross_phase_replan"
                    if stale
                    else "execute_ready_operations"
                )
                if action_kind != expected:
                    raise ValueError("preflight action disagrees with target version")
                if stale:
                    assert plan is not None
                    await AgentGraphRepository(session).record_human_gate(
                        graph_run_id=context.graph_run_id,
                        cursor=context.graph_cursor,
                        gate_kind="cross_phase_replan",
                        member_ids=(str(plan.id),),
                        content_hash=_hash(
                            {
                                "plan_id": str(plan.id),
                                "planned_target_version": plan.target_version,
                                "current_target_version": (
                                    f"sha256:{current.file_sha256}"
                                    if current is not None
                                    else None
                                ),
                            }
                        ),
                        status="pending",
                    )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output={
                        "target_version_current": not stale,
                        "replan_confirmation_required": stale,
                    },
                )
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=action.required_evidence,
            pause_for_human=stale,
        )

    async def _execute_governance(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        model_failure: GraphSubAgentFailure | None = None
        async with self._session_factory() as session:
            async with session.begin():
                plan = await session.scalar(
                    select(AgentGovernancePlanRecord)
                    .where(AgentGovernancePlanRecord.run_id == context.run_id)
                    .order_by(AgentGovernancePlanRecord.created_at.desc())
                )
                if plan is None:
                    await self._record_deterministic_invocation(
                        session,
                        context=context,
                        action=action,
                        output={"operation_count": 0},
                    )
                    return _outcome(action)
                operation_ids = tuple(
                    _resource_uuid(item, "operation")
                    for item in action.resource_ids
                    if item.startswith("operation:")
                )
                if not operation_ids:
                    await self._record_deterministic_invocation(
                        session,
                        context=context,
                        action=action,
                        output={"operation_count": 0, "plan_id": str(plan.id)},
                    )
                    return _outcome(action)
                task = await session.get(ReconciliationTask, context.task_id)
                database_task = _task_uses_database(task)
                target_connector: ConfiguredApiConnector | None = None
                if not database_task and not self._csv_execution_enabled:
                    raise RuntimeError(
                        "Agent graph CSV execution is disabled before a writable plan"
                    )
                if database_task and self._sql_governance is None:
                    raise RuntimeError(
                        "Agent graph SQL execution is disabled before a writable plan"
                    )
                if (
                    database_task
                    and context.ingestion_contract_version == "source-ingestion-v3"
                ):
                    target_binding = next(
                        binding
                        for binding in await load_source_bindings(
                            session,
                            task_id=context.task_id,
                            tenant_id=context.tenant_id,
                        )
                        if binding.role == "target"
                    )
                    target_connector = await self._database_connector_for_binding(
                        target_binding
                    )

                async def execute_operation(operation_id: UUID) -> OperationOutcome:
                    legacy_context = _legacy_context(
                        context,
                        AgentPhase.EXECUTE_AND_VERIFY,
                    )
                    if database_task:
                        assert self._sql_governance is not None
                        record = await self._sql_governance.execute_operation(
                            session,
                            legacy_context,
                            operation_id=operation_id,
                            connector_override=target_connector,
                        )
                    else:
                        record = await self._governance.execute_operation(
                            session,
                            legacy_context,
                            operation_id=operation_id,
                        )
                    if record is None or record.run_id != context.run_id:
                        raise LookupError("executed operation fact is missing")
                    status = _operation_status(record.status)
                    return OperationOutcome(
                        operation_id=record.id,
                        status=status,
                        verification_ref=(
                            f"verification:{record.id}"
                            if status == "succeeded"
                            else None
                        ),
                        safe_error_code=(
                            None
                            if status == "succeeded"
                            else str(
                                (record.verification or {}).get(
                                    "safe_error_code",
                                    "target_write_failed",
                                )
                            )
                        ),
                    )

                if context.execution_contract_version == "deterministic-execution-v2":
                    outcomes = tuple(
                        [
                            await execute_operation(operation_id)
                            for operation_id in operation_ids
                        ]
                    )
                    await self._record_deterministic_invocation(
                        session,
                        context=context,
                        action=action,
                        output={
                            "plan_id": str(plan.id),
                            "outcomes": [
                                outcome.model_dump(mode="json") for outcome in outcomes
                            ],
                        },
                    )
                    return _outcome(action)

                bound_action = action.model_copy(
                    update={
                        "resource_ids": (
                            f"execution-plan:{plan.id}",
                            *(f"operation:{item}" for item in operation_ids),
                        ),
                        "required_evidence": tuple(
                            f"execution-outcome:{item}" for item in operation_ids
                        ),
                    }
                )
                manifest_id = await _record_manifest(
                    session,
                    context=context,
                    action=bound_action,
                    tokenization_secret=self._tokenization_secret,
                )
                operator = OperatorContext(
                    operator_id=context.worker_id,
                    tenant_id=context.tenant_id,
                )
                tools = GraphExecutionTools(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    tenant_id=context.tenant_id,
                    plan_id=plan.id,
                    operation_ids=operation_ids,
                    execute_operation=execute_operation,
                )
                runner = GraphSkillModelRunner(
                    session,
                    provider=self._provider,
                    tool_gateway=GraphPhaseToolGateway(
                        session,
                        operator=operator,
                        tools=tools.handlers(),
                    ),
                    operator=operator,
                    max_retries=self._max_retries,
                )
                try:
                    await GraphGovernanceExecutionExecutor(
                        runner=runner,
                        tools=tools,
                    ).run(
                        GraphSkillInvocation(
                            task_id=context.task_id,
                            run_id=context.run_id,
                            graph_run_id=context.graph_run_id,
                            graph_node=context.current_node,
                            graph_cursor=context.graph_cursor,
                            action_id=action.action_id,
                            evidence_manifest_id=manifest_id,
                            skill_name="execute-approved-governance-plan",
                            skill_version="1.0.0",
                            input_payload=GovernanceExecutionInput(
                                task_id=context.task_id,
                                run_id=context.run_id,
                                phase=AgentPhase.EXECUTE_AND_VERIFY,
                                evidence_refs=bound_action.required_evidence,
                                plan_id=plan.id,
                                operation_ids=operation_ids,
                            ).model_dump(mode="json"),
                        )
                    )
                except GraphSubAgentFailure as error:
                    model_failure = error
        if model_failure is not None:
            raise model_failure
        return _outcome(action)

    async def _generate_report(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                facts = await build_agent_report_facts(
                    session,
                    run_id=context.run_id,
                )
                task = await session.get(ReconciliationTask, context.task_id)
                if self._settings is not None and not _task_uses_database(task):
                    output_version_id = facts.get("output_target_version_id")
                    facts["publication"] = await publish_local_target(
                        session,
                        settings=self._settings,
                        task_id=context.task_id,
                        run_id=context.run_id,
                        phase=AgentPhase.GENERATE_REPORT,
                        target_version_id=(
                            UUID(str(output_version_id))
                            if output_version_id is not None
                            else None
                        ),
                    )
                terminal_state = (
                    "abnormal_input"
                    if context.current_node == "abnormal_input_report"
                    else "terminated"
                    if context.current_node == "termination_report"
                    else "completed"
                )
                fact_ref = f"report-facts:{context.run_id}:{context.graph_cursor}"
                if terminal_state == "terminated":
                    termination_context = _termination_report_context(context, facts)
                    facts["termination_context"] = termination_context
                    failure = facts.get("latest_failure")
                    progress = facts.get("analysis_progress")
                    if not isinstance(failure, Mapping) or not isinstance(
                        progress, Mapping
                    ):
                        await AgentReportingService(session).generate(
                            task_id=context.task_id,
                            tenant_id=context.tenant_id,
                            kind="sync",
                            terminal_state=terminal_state,
                            facts=facts,
                            narrative={
                                "title_zh": "任务终止报告",
                                "summary_zh": _termination_report_summary(
                                    termination_context
                                ),
                                "fact_refs": [fact_ref],
                                "degraded": False,
                            },
                            generated_by=(
                                "agent-graph-termination-fallback-v1"
                            ),
                        )
                        await AgentRuntimeRepository(session).append_event(
                            context.run_id,
                            "termination.report.deterministic",
                            {
                                "phase": AgentPhase.GENERATE_REPORT.value,
                                "status": "terminated",
                            },
                        )
                        return _outcome(action)

                    bound_action = action.model_copy(
                        update={
                            "resource_ids": (fact_ref,),
                            "required_evidence": (fact_ref,),
                        }
                    )
                    manifest_id = await _record_manifest(
                        session,
                        context=context,
                        action=bound_action,
                        tokenization_secret=self._tokenization_secret,
                    )
                    operator = OperatorContext(
                        operator_id=context.worker_id,
                        tenant_id=context.tenant_id,
                    )
                    runner = GraphSkillModelRunner(
                        session,
                        provider=self._provider,
                        tool_gateway=GraphPhaseToolGateway(
                            session,
                            operator=operator,
                            tools={},
                        ),
                        operator=operator,
                        max_retries=0,
                    )

                    def validate_failure_analysis(output: BaseModel) -> BaseModel:
                        if not isinstance(output, FailureAnalysisOutput):
                            raise ValueError(
                                "failure analysis Skill returned another schema"
                            )
                        if output.fact_refs != (fact_ref,):
                            raise ValueError(
                                "failure analysis changed frozen fact references"
                            )
                        return output

                    try:
                        result = await runner.run(
                            GraphSkillInvocation(
                                task_id=context.task_id,
                                run_id=context.run_id,
                                graph_run_id=context.graph_run_id,
                                graph_node=context.current_node,
                                graph_cursor=context.graph_cursor,
                                action_id=action.action_id,
                                evidence_manifest_id=manifest_id,
                                skill_name="analyze-agent-failure",
                                skill_version="1.0.0",
                                input_payload=FailureAnalysisInput(
                                    task_id=context.task_id,
                                    run_id=context.run_id,
                                    phase=AgentPhase.GENERATE_REPORT,
                                    evidence_refs=(fact_ref,),
                                    fact_refs=(fact_ref,),
                                    failure=failure,
                                    analysis_progress=progress,
                                ).model_dump(mode="json"),
                            ),
                            result_validator=validate_failure_analysis,
                        )
                        output = result.output
                        if not isinstance(output, FailureAnalysisOutput):
                            raise RuntimeError(
                                "validated failure analysis changed type"
                            )
                        await AgentReportingService(session).generate(
                            task_id=context.task_id,
                            tenant_id=context.tenant_id,
                            kind="sync",
                            terminal_state=terminal_state,
                            facts=facts,
                            narrative={
                                "reason_code": output.reason_code,
                                "title_zh": output.title_zh,
                                "summary_zh": output.summary_zh,
                                "impact_zh": output.impact_zh,
                                "suggestion_zh": output.suggestion_zh,
                                "fact_refs": list(output.fact_refs),
                                "degraded": False,
                            },
                            generated_by=(
                                "agent-graph-failure-analysis-skill-v1"
                            ),
                        )
                        await AgentRuntimeRepository(session).append_event(
                            context.run_id,
                            "termination.failure_analysis.completed",
                            {
                                "phase": AgentPhase.GENERATE_REPORT.value,
                                "status": "terminated",
                                "failure_code": str(failure.get("code", ""))[
                                    :128
                                ],
                            },
                        )
                    except GraphSubAgentFailure:
                        deterministic = _deterministic_failure_analysis(
                            termination_context
                        )
                        await AgentReportingService(session).generate(
                            task_id=context.task_id,
                            tenant_id=context.tenant_id,
                            kind="sync",
                            terminal_state=terminal_state,
                            facts=facts,
                            narrative={
                                **deterministic,
                                "fact_refs": [fact_ref],
                                "degraded": True,
                            },
                            generated_by=(
                                "agent-graph-failure-analysis-fallback-v1"
                            ),
                        )
                        await AgentRuntimeRepository(session).append_event(
                            context.run_id,
                            "termination.failure_analysis.fallback",
                            {
                                "phase": AgentPhase.GENERATE_REPORT.value,
                                "status": "terminated",
                                "safe_error_code": (
                                    "failure_analysis_model_unavailable"
                                ),
                            },
                        )
                    return _outcome(action)
                bound_action = action.model_copy(
                    update={
                        "resource_ids": (fact_ref,),
                        "required_evidence": (fact_ref,),
                    }
                )
                manifest_id = await _record_manifest(
                    session,
                    context=context,
                    action=bound_action,
                    tokenization_secret=self._tokenization_secret,
                )
                operator = OperatorContext(
                    operator_id=context.worker_id,
                    tenant_id=context.tenant_id,
                )
                fact_tools = GraphReportFactTools(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    tenant_id=context.tenant_id,
                    resource_id=fact_ref,
                    facts=facts,
                )
                runner = GraphSkillModelRunner(
                    session,
                    provider=self._provider,
                    tool_gateway=GraphPhaseToolGateway(
                        session,
                        operator=operator,
                        tools=fact_tools.handlers(),
                    ),
                    operator=operator,
                    max_retries=self._max_retries,
                )
                rollback_eligible = has_fully_verified_mutations(
                    terminal_state,
                    facts,
                )
                invocation = GraphSkillInvocation(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    graph_run_id=context.graph_run_id,
                    graph_node=context.current_node,
                    graph_cursor=context.graph_cursor,
                    action_id=action.action_id,
                    evidence_manifest_id=manifest_id,
                    skill_name="generate-agent-governance-report",
                    skill_version="1.0.0",
                    input_payload=GovernanceReportInput(
                        task_id=context.task_id,
                        run_id=context.run_id,
                        phase=AgentPhase.GENERATE_REPORT,
                        evidence_refs=(fact_ref,),
                        outcome=terminal_state,
                        fact_refs=(fact_ref,),
                    ).model_dump(mode="json"),
                )
                try:
                    await GraphReportExecutor(session, runner=runner).generate(
                        invocation,
                        tenant_id=context.tenant_id,
                        kind="sync",
                        terminal_state=terminal_state,
                        facts=facts,
                        expected_rollback_eligible=rollback_eligible,
                    )
                except GraphSubAgentFailure:
                    await AgentReportingService(session).generate(
                        task_id=context.task_id,
                        tenant_id=context.tenant_id,
                        kind="sync",
                        terminal_state=terminal_state,
                        facts=facts,
                        narrative={
                            "title_zh": (
                                "输入异常报告"
                                if terminal_state == "abnormal_input"
                                else "数据同步分析报告"
                            ),
                            "summary_zh": (
                                "模型报告暂不可用，本报告仅保留服务端已验证事实。"
                            ),
                            "fact_refs": [fact_ref],
                            "degraded": True,
                            "input_exception_analyses": (
                                _deterministic_input_exception_analyses(facts)
                                if terminal_state == "abnormal_input"
                                else []
                            ),
                        },
                        generated_by="agent-graph-report-fallback-v1",
                    )
                    await AgentRuntimeRepository(session).append_event(
                        context.run_id,
                        "report.fallback",
                        {
                            "phase": AgentPhase.GENERATE_REPORT.value,
                            "status": terminal_state,
                            "safe_error_code": "report_model_unavailable",
                        },
                    )
        return _outcome(action)

    async def _plan_rollback(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                task = await session.get(
                    ReconciliationTask,
                    context.task_id,
                )
                if _task_uses_database(task):
                    if self._sql_rollback is None:
                        raise RuntimeError(
                            "SQL rollback connector runtime is unavailable"
                        )
                    await self._sql_rollback.plan(
                        session,
                        _legacy_context(
                            context,
                            AgentPhase.PLAN_RESTORE,
                        ),
                    )
                else:
                    await self._rollback.plan(
                        session,
                        _legacy_context(
                            context,
                            AgentPhase.PLAN_RESTORE,
                        ),
                    )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output={"verified_mutations": "loaded"},
                )
        return _outcome(action)

    async def _assess_rollback(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                task = await session.get(ReconciliationTask, context.task_id)
                if task is None or not task.agent_intent:
                    raise LookupError("rollback task facts are missing")
                mutations = tuple(
                    dict(item) for item in task.agent_intent.get("operations", [])
                )
                restore_comparisons = tuple(
                    dict(item)
                    for item in task.agent_intent.get(
                        "restore_comparisons",
                        [],
                    )
                )
                operation_ids = tuple(UUID(str(item["id"])) for item in mutations)
                original_task_id = UUID(str(task.agent_intent["source_task_id"]))
                resource_id = f"rollback-facts:{context.run_id}"
                bound_action = action.model_copy(
                    update={
                        "resource_ids": (resource_id,),
                        "required_evidence": tuple(
                            f"verified-mutation:{item}" for item in operation_ids
                        ),
                    }
                )
                manifest_id = await _record_manifest(
                    session,
                    context=context,
                    action=bound_action,
                    tokenization_secret=self._tokenization_secret,
                )
                operator = OperatorContext(
                    operator_id=context.worker_id,
                    tenant_id=context.tenant_id,
                )
                evidence_tools = GraphRollbackEvidenceTools(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    tenant_id=context.tenant_id,
                    resource_id=resource_id,
                    verified_mutations=mutations,
                    restore_comparisons=restore_comparisons,
                )
                runner = GraphSkillModelRunner(
                    session,
                    provider=self._provider,
                    tool_gateway=GraphPhaseToolGateway(
                        session,
                        operator=operator,
                        tools=evidence_tools.handlers(),
                    ),
                    operator=operator,
                    max_retries=self._max_retries,
                )
                assessment = await GraphRollbackAssessmentExecutor(runner=runner).run(
                    GraphSkillInvocation(
                        task_id=context.task_id,
                        run_id=context.run_id,
                        graph_run_id=context.graph_run_id,
                        graph_node=context.current_node,
                        graph_cursor=context.graph_cursor,
                        action_id=action.action_id,
                        evidence_manifest_id=manifest_id,
                        skill_name="assess-agent-rollback-impact",
                        skill_version="2.1.0",
                        input_payload=RollbackAssessmentInput(
                            task_id=context.task_id,
                            run_id=context.run_id,
                            phase=AgentPhase.PLAN_RESTORE,
                            evidence_refs=bound_action.required_evidence,
                            original_task_id=original_task_id,
                            verified_execution_refs=bound_action.required_evidence,
                        ).model_dump(mode="json"),
                    ),
                    operation_ids=operation_ids,
                    restore_comparisons=restore_comparisons,
                )
                if assessment.conflict_operation_ids:
                    await AgentGraphRepository(session).record_human_gate(
                        graph_run_id=context.graph_run_id,
                        cursor=context.graph_cursor,
                        gate_kind="rollback_conflict",
                        member_ids=tuple(
                            str(item) for item in assessment.conflict_operation_ids
                        ),
                        content_hash=_hash(
                            {
                                "conflict_operation_ids": [
                                    str(item)
                                    for item in assessment.conflict_operation_ids
                                ],
                                "impact_zh": assessment.impact_zh,
                            }
                        ),
                        status="pending",
                    )
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=action.required_evidence,
            pause_for_human=bool(assessment.conflict_operation_ids),
        )

    async def _enter_rollback_approval(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                conflict_statuses = tuple(
                    await session.scalars(
                        select(AgentHumanGateRecord.status).where(
                            AgentHumanGateRecord.graph_run_id == context.graph_run_id,
                            AgentHumanGateRecord.gate_kind == "rollback_conflict",
                        )
                    )
                )
                if any(status != "approved" for status in conflict_statuses):
                    raise ValueError("rollback conflicts are not approved")
                task = await session.get(ReconciliationTask, context.task_id)
                if task is None or not task.agent_intent:
                    raise LookupError("rollback task facts are missing")
                operation_ids = tuple(
                    str(item["id"]) for item in task.agent_intent.get("operations", [])
                )
                if not operation_ids:
                    raise ValueError("rollback approval has no verified operations")
                await AgentGraphRepository(session).record_human_gate(
                    graph_run_id=context.graph_run_id,
                    cursor=context.graph_cursor,
                    gate_kind="rollback_approval",
                    member_ids=operation_ids,
                    content_hash=_hash(
                        {
                            "operation_ids": list(operation_ids),
                            "requires_approval": True,
                        }
                    ),
                    status="pending",
                )
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=action.required_evidence,
            pause_for_human=True,
        )

    async def _compile_rollback(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                gates = tuple(
                    await session.scalars(
                        select(AgentHumanGateRecord).where(
                            AgentHumanGateRecord.graph_run_id == context.graph_run_id,
                            AgentHumanGateRecord.gate_kind.in_(
                                ("rollback_conflict", "rollback_approval")
                            ),
                        )
                    )
                )
                approval = tuple(
                    gate for gate in gates if gate.gate_kind == "rollback_approval"
                )
                if len(approval) != 1 or approval[0].status != "approved":
                    raise ValueError("rollback requires one approved frozen gate")
                if any(
                    gate.status != "approved"
                    for gate in gates
                    if gate.gate_kind == "rollback_conflict"
                ):
                    raise ValueError("rollback conflict is not approved")
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output={"restore_plan": "compiled_from_verified_facts"},
                )
        return _outcome(action)

    async def _execute_deterministic_rollback(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                runtime = AgentRuntimeRepository(session)
                for completed_key in (
                    "agent-sql-rollback-execution-v2",
                    "agent-csv-rollback-execution-v1",
                ):
                    completed = await runtime.get_checkpoint(
                        context.run_id,
                        phase=AgentPhase.EXECUTE_RESTORE,
                        checkpoint_key=completed_key,
                    )
                    if completed is not None:
                        return _outcome(action)
                task = await session.get(
                    ReconciliationTask,
                    context.task_id,
                )
                if task is None or not task.agent_intent:
                    raise LookupError("rollback task facts are missing")
                parent = await session.get(
                    TargetVersionRecord,
                    UUID(str(task.agent_intent["target_version_id"])),
                )
                if parent is None:
                    raise LookupError("rollback target version is missing")
                operations = _rollback_operations(
                    tuple(task.agent_intent.get("operations", [])),
                    target_version=f"sha256:{parent.file_sha256}",
                )
                if not operations:
                    raise ValueError("rollback has no verified operations")
                operation_ids = tuple(item.id for item in operations)
                source_task_id = str(task.parent_task_id)
                request_hash = str(task.request_hash)
                plan_id = uuid5(
                    NAMESPACE_URL,
                    f"agent-rollback:{task.id}",
                )
                database_rollback = _task_uses_database(task)
                if database_rollback and self._sql_rollback is None:
                    raise RuntimeError("SQL rollback connector runtime is unavailable")
                checkpoint_key = (
                    "agent-sql-rollback-execution-v2"
                    if database_rollback
                    else "agent-csv-rollback-execution-v1"
                )

        mutation_facts: list[dict[str, object]] = []
        for operation_id in operation_ids:
            async with self._session_factory() as session:
                async with session.begin():
                    legacy_context = _legacy_context(
                        context,
                        AgentPhase.EXECUTE_RESTORE,
                    )
                    if database_rollback:
                        assert self._sql_rollback is not None
                        fact = await self._sql_rollback.execute_operation(
                            session,
                            legacy_context,
                            operation_id,
                        )
                    else:
                        fact = await self._rollback.execute_operation(
                            session,
                            legacy_context,
                            operation_id,
                        )
                    mutation_facts.append(fact)

        facts: dict[str, object] = {
            "source_task_id": source_task_id,
            "mutations": mutation_facts,
        }
        output_fact = next(
            (
                item
                for item in reversed(mutation_facts)
                if item.get("output_target_version_id")
            ),
            None,
        )
        if output_fact is not None:
            facts.update(
                {
                    "output_target_version_id": output_fact["output_target_version_id"],
                    "output_target_path": output_fact["output_target_path"],
                }
            )
        async with self._session_factory() as session:
            async with session.begin():
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.EXECUTE_RESTORE,
                    checkpoint_key=checkpoint_key,
                    input_hash=request_hash,
                    payload=facts,
                )
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output={
                        "restore_plan_id": str(plan_id),
                        "operation_count": len(operation_ids),
                        "outcome_statuses": [
                            str(item.get("status", "skipped"))
                            for item in mutation_facts
                        ],
                    },
                )
        return _outcome(action)

    async def _execute_rollback(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        if context.execution_contract_version == "deterministic-execution-v2":
            return await self._execute_deterministic_rollback(
                context,
                action,
            )
        async with self._session_factory() as session:
            async with session.begin():
                runtime = AgentRuntimeRepository(session)
                for completed_key in (
                    "agent-sql-rollback-execution-v2",
                    "agent-csv-rollback-execution-v1",
                ):
                    completed = await runtime.get_checkpoint(
                        context.run_id,
                        phase=AgentPhase.EXECUTE_RESTORE,
                        checkpoint_key=completed_key,
                    )
                    if completed is not None:
                        return _outcome(action)
                task = await session.get(ReconciliationTask, context.task_id)
                if task is None or not task.agent_intent:
                    raise LookupError("rollback task facts are missing")
                parent = await session.get(
                    TargetVersionRecord,
                    UUID(str(task.agent_intent["target_version_id"])),
                )
                if parent is None:
                    raise LookupError("rollback target version is missing")
                operations = _rollback_operations(
                    tuple(task.agent_intent.get("operations", [])),
                    target_version=f"sha256:{parent.file_sha256}",
                )
                if not operations:
                    raise ValueError("rollback has no verified operations")
                operation_ids = tuple(item.id for item in operations)
                plan_id = uuid5(NAMESPACE_URL, f"agent-rollback:{task.id}")
                database_rollback = _task_uses_database(task)
                if database_rollback and self._sql_rollback is None:
                    raise RuntimeError("SQL rollback connector runtime is unavailable")

                async def execute_operation(operation_id: UUID) -> OperationOutcome:
                    legacy_context = _legacy_context(
                        context,
                        AgentPhase.EXECUTE_RESTORE,
                    )
                    if database_rollback:
                        assert self._sql_rollback is not None
                        fact = await self._sql_rollback.execute_operation(
                            session,
                            legacy_context,
                            operation_id,
                        )
                    else:
                        fact = await self._rollback.execute_operation(
                            session,
                            legacy_context,
                            operation_id,
                        )
                    status = _operation_status(str(fact["status"]))
                    return OperationOutcome(
                        operation_id=operation_id,
                        status=status,
                        verification_ref=(
                            f"verification:{operation_id}"
                            if status
                            in {
                                "succeeded",
                                "already_restored",
                            }
                            else None
                        ),
                        safe_error_code=(
                            None
                            if status
                            in {
                                "succeeded",
                                "already_restored",
                            }
                            else str(
                                fact.get(
                                    "safe_error_code",
                                    "rollback_target_write_failed",
                                )
                            )
                        ),
                    )

                resources = (
                    f"execution-plan:{plan_id}",
                    *(f"operation:{item}" for item in operation_ids),
                )
                bound_action = action.model_copy(
                    update={
                        "resource_ids": resources,
                        "required_evidence": ("rollback-outcomes:v1",),
                    }
                )
                manifest_id = await _record_manifest(
                    session,
                    context=context,
                    action=bound_action,
                    tokenization_secret=self._tokenization_secret,
                )
                operator = OperatorContext(
                    operator_id=context.worker_id,
                    tenant_id=context.tenant_id,
                )
                tools = GraphExecutionTools(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    tenant_id=context.tenant_id,
                    plan_id=plan_id,
                    operation_ids=operation_ids,
                    execute_operation=execute_operation,
                )
                runner = GraphSkillModelRunner(
                    session,
                    provider=self._provider,
                    tool_gateway=GraphPhaseToolGateway(
                        session,
                        operator=operator,
                        tools=tools.handlers(),
                    ),
                    operator=operator,
                    max_retries=self._max_retries,
                )
                await GraphRollbackExecutionExecutor(
                    runner=runner,
                    tools=tools,
                ).run(
                    GraphSkillInvocation(
                        task_id=context.task_id,
                        run_id=context.run_id,
                        graph_run_id=context.graph_run_id,
                        graph_node=context.current_node,
                        graph_cursor=context.graph_cursor,
                        action_id=action.action_id,
                        evidence_manifest_id=manifest_id,
                        skill_name="execute-approved-rollback",
                        skill_version="2.1.0",
                        input_payload=RollbackExecutionInput(
                            task_id=context.task_id,
                            run_id=context.run_id,
                            phase=AgentPhase.EXECUTE_RESTORE,
                            evidence_refs=("rollback-outcomes:v1",),
                            restore_plan_id=plan_id,
                            operation_ids=operation_ids,
                        ).model_dump(mode="json"),
                    )
                )
                await self._rollback.execute(
                    session,
                    _legacy_context(
                        context,
                        AgentPhase.EXECUTE_RESTORE,
                    ),
                )
        return _outcome(action)

    async def _generate_rollback_report(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                runtime = AgentRuntimeRepository(session)
                checkpoint = None
                for checkpoint_key in (
                    "agent-sql-rollback-execution-v2",
                    "agent-csv-rollback-execution-v1",
                ):
                    checkpoint = await runtime.get_checkpoint(
                        context.run_id,
                        phase=AgentPhase.EXECUTE_RESTORE,
                        checkpoint_key=checkpoint_key,
                    )
                    if checkpoint is not None:
                        break
                facts = (
                    dict(checkpoint.payload)
                    if checkpoint is not None
                    else {"mutations": []}
                )
                task = await session.get(ReconciliationTask, context.task_id)
                if self._settings is not None and not _task_uses_database(task):
                    output_version_id = facts.get("output_target_version_id")
                    facts["publication"] = await publish_local_target(
                        session,
                        settings=self._settings,
                        task_id=context.task_id,
                        run_id=context.run_id,
                        phase=AgentPhase.REPORT_RESTORE,
                        target_version_id=(
                            UUID(str(output_version_id))
                            if output_version_id is not None
                            else None
                        ),
                    )
                fact_ref = f"report-facts:{context.run_id}:{context.graph_cursor}"
                bound_action = action.model_copy(
                    update={
                        "resource_ids": (fact_ref,),
                        "required_evidence": (fact_ref,),
                    }
                )
                manifest_id = await _record_manifest(
                    session,
                    context=context,
                    action=bound_action,
                    tokenization_secret=self._tokenization_secret,
                )
                operator = OperatorContext(
                    operator_id=context.worker_id,
                    tenant_id=context.tenant_id,
                )
                tools = GraphReportFactTools(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    tenant_id=context.tenant_id,
                    resource_id=fact_ref,
                    facts=facts,
                )
                runner = GraphSkillModelRunner(
                    session,
                    provider=self._provider,
                    tool_gateway=GraphPhaseToolGateway(
                        session,
                        operator=operator,
                        tools=tools.handlers(),
                    ),
                    operator=operator,
                    max_retries=0,
                )
                terminal_state = rollback_terminal_state(facts)
                rollback_eligible = has_fully_verified_mutations(
                    terminal_state,
                    facts,
                )
                invocation = GraphSkillInvocation(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    graph_run_id=context.graph_run_id,
                    graph_node=context.current_node,
                    graph_cursor=context.graph_cursor,
                    action_id=action.action_id,
                    evidence_manifest_id=manifest_id,
                    skill_name="generate-agent-governance-report",
                    skill_version="1.0.0",
                    input_payload=GovernanceReportInput(
                        task_id=context.task_id,
                        run_id=context.run_id,
                        phase=AgentPhase.GENERATE_REPORT,
                        evidence_refs=(fact_ref,),
                        outcome=(
                            "failed"
                            if terminal_state == "completed_with_conflicts"
                            else terminal_state
                        ),
                        fact_refs=(fact_ref,),
                    ).model_dump(mode="json"),
                )
                try:
                    await GraphReportExecutor(session, runner=runner).generate(
                        invocation,
                        tenant_id=context.tenant_id,
                        kind="rollback",
                        terminal_state=terminal_state,
                        facts=facts,
                        expected_rollback_eligible=rollback_eligible,
                    )
                except GraphSubAgentFailure:
                    await AgentReportingService(session).generate(
                        task_id=context.task_id,
                        tenant_id=context.tenant_id,
                        kind="rollback",
                        terminal_state=terminal_state,
                        facts=facts,
                        narrative={
                            "title_zh": "数据回滚报告",
                            "summary_zh": (
                                "模型报告暂不可用，本报告仅保留服务端已验证的回滚事实。"
                            ),
                            "fact_refs": [fact_ref],
                            "degraded": True,
                        },
                        generated_by="agent-graph-rollback-report-fallback-v1",
                    )
                    await AgentRuntimeRepository(session).append_event(
                        context.run_id,
                        "rollback.report.fallback",
                        {
                            "phase": AgentPhase.REPORT_RESTORE.value,
                            "status": terminal_state,
                            "safe_error_code": "report_model_unavailable",
                        },
                    )
        return _outcome(action)

    async def _record_guarded_noop(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                await self._record_deterministic_invocation(
                    session,
                    context=context,
                    action=action,
                    output={
                        "guarded_action": action.graph_action_kind or action.action_id
                    },
                )
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=action.required_evidence,
            pause_for_human=action.kind == "wait_human",
        )

    async def _analysis_runtime(
        self,
        session: AsyncSession,
        *,
        context: GraphWorkContext,
        action: AllowedActionV1,
        prepare_sensitive_tokens: bool = True,
        durable_tool_recovery: bool = False,
    ) -> tuple[GraphAnalysisEvidenceTools, GraphSkillModelRunner, UUID]:
        operator = OperatorContext(
            operator_id=context.worker_id,
            tenant_id=context.tenant_id,
        )
        tools = GraphAnalysisEvidenceTools(
            session,
            task_id=context.task_id,
            run_id=context.run_id,
            tenant_id=context.tenant_id,
            tokenization_secret=self._tokenization_secret,
        )
        issued_sensitive_tokens = (
            await tools.prepare_manifest_tokens(action.resource_ids)
            if prepare_sensitive_tokens
            else ()
        )
        manifest_id = await _record_manifest(
            session,
            context=context,
            action=action,
            tokenization_secret=self._tokenization_secret,
            issued_sensitive_tokens=issued_sensitive_tokens,
        )
        gateway = GraphPhaseToolGateway(
            session,
            operator=operator,
            tools=tools.handlers(),
        )
        return (
            tools,
            GraphSkillModelRunner(
                session,
                provider=self._provider,
                tool_gateway=gateway,
                operator=operator,
                max_retries=self._max_retries,
                durable_tool_recovery=durable_tool_recovery,
            ),
            manifest_id,
        )

    async def _record_deterministic_invocation(
        self,
        session: AsyncSession,
        *,
        context: GraphWorkContext,
        action: AllowedActionV1,
        output: dict[str, object],
    ) -> None:
        manifest_id = await _record_manifest(
            session,
            context=context,
            action=action,
            tokenization_secret=self._tokenization_secret,
        )
        await AgentGraphRepository(session).record_invocation(
            graph_run_id=context.graph_run_id,
            cursor=context.graph_cursor,
            action_id=action.action_id,
            evidence_manifest_id=manifest_id,
            execution_mode="deterministic_guarded",
            skill_name="server-guard",
            skill_version="1.0.0",
            schema_version="server-fact-v1",
            attempt=1,
            status="completed",
            input_hash=_hash(action.model_dump(mode="json")),
            output_hash=_hash(output),
            model_provenance={"provider": "server", "model": "none"},
        )


async def _record_manifest(
    session: AsyncSession,
    *,
    context: GraphWorkContext,
    action: AllowedActionV1,
    tokenization_secret: str,
    issued_sensitive_tokens: tuple[str, ...] = (),
) -> UUID:
    snapshots = tuple(
        await session.scalars(
            select(Snapshot)
            .where(Snapshot.task_id == context.task_id)
            .order_by(Snapshot.source_role)
        )
    )
    snapshots_by_role = {snapshot.source_role: snapshot for snapshot in snapshots}
    authority_snapshot = snapshots_by_role.get("authoritative")
    target_snapshot = snapshots_by_role.get("target")
    snapshot_pair = (
        (str(authority_snapshot.id), str(target_snapshot.id))
        if authority_snapshot is not None and target_snapshot is not None
        else None
    )
    current_target = await ExecutionRepository(session).current_target_version(
        context.task_id
    )
    target_version = (
        f"sha256:{current_target.file_sha256}"
        if current_target is not None
        else (
            f"sha256:{target_snapshot.file_hash}"
            if target_snapshot is not None
            else None
        )
    )
    existing_record = await session.scalar(
        select(AgentEvidenceManifestRecord).where(
            AgentEvidenceManifestRecord.graph_run_id == context.graph_run_id,
            AgentEvidenceManifestRecord.cursor == context.graph_cursor,
            AgentEvidenceManifestRecord.action_id == action.action_id,
        )
    )
    existing_manifest = (
        EvidenceManifestV1.model_validate(existing_record.manifest)
        if existing_record is not None
        else None
    )
    manifest = build_evidence_manifest(
        tenant_ref=opaque_tenant_ref(
            secret=tokenization_secret,
            tenant_id=context.tenant_id,
        ),
        task_id=str(context.task_id),
        run_id=str(context.run_id),
        graph_node=context.current_node,
        action_id=action.action_id,
        snapshot_pair=snapshot_pair,
        target_version=target_version,
        resource_ids=action.resource_ids,
        allowed_evidence_refs=action.required_evidence,
        issued_sensitive_tokens=issued_sensitive_tokens,
        manifest_id=existing_manifest.manifest_id if existing_manifest else None,
        created_at=existing_manifest.created_at if existing_manifest else None,
    )
    if existing_record is not None:
        if existing_record.content_hash != manifest.content_hash:
            raise GraphFactConflict("evidence manifest replay changed frozen content")
        return existing_record.id
    await AgentGraphRepository(session).record_manifest(
        graph_run_id=context.graph_run_id,
        cursor=context.graph_cursor,
        graph_node=context.current_node,
        action_id=action.action_id,
        manifest=manifest.model_dump(mode="json"),
        content_hash=manifest.content_hash,
        record_id=manifest.manifest_id,
    )
    return manifest.manifest_id


async def _page_locators(
    session: AsyncSession,
    *,
    task_id: UUID,
    role: str,
    page: int,
) -> tuple[str, ...]:
    source = await _source(session, task_id=task_id, role=role)
    path = Path(source.storage_path)
    frame = read_csv_frame(path, inspect_csv(path))
    return tuple(
        f"csv:{int(row['_row_number'])}"
        for row in frame.slice((page - 1) * 50, 50).to_dicts()
    )


async def _snapshot_id(
    session: AsyncSession,
    *,
    task_id: UUID,
    role: str,
) -> UUID:
    value = await session.scalar(
        select(Snapshot.id).where(
            Snapshot.task_id == task_id,
            Snapshot.source_role == role,
        )
    )
    if value is None:
        raise LookupError("Agent snapshot is missing")
    return value


async def _source(
    session: AsyncSession,
    *,
    task_id: UUID,
    role: str,
) -> SourceFile:
    source = await session.scalar(
        select(SourceFile)
        .join(Snapshot, Snapshot.source_file_id == SourceFile.id)
        .where(
            Snapshot.task_id == task_id,
            Snapshot.source_role == role,
            SourceFile.task_id == task_id,
            SourceFile.source_role == role,
        )
    )
    if source is None:
        raise LookupError("Agent source file is missing")
    return source


async def _source_snapshot(
    session: AsyncSession,
    *,
    task_id: UUID,
    role: str,
) -> tuple[Snapshot, SourceFile]:
    row = (
        await session.execute(
            select(Snapshot, SourceFile)
            .join(SourceFile, Snapshot.source_file_id == SourceFile.id)
            .where(
                Snapshot.task_id == task_id,
                Snapshot.source_role == role,
                SourceFile.task_id == task_id,
                SourceFile.source_role == role,
            )
        )
    ).one_or_none()
    if row is None:
        raise LookupError("Agent source snapshot is missing")
    return row[0], row[1]


async def _api_artifact_materials(
    session: AsyncSession,
    *,
    task_id: UUID,
    tenant_id: str,
    binding: AgentSourceBinding,
) -> tuple[
    ApiAuthoritySourceRecord,
    ApiConnectionRecord,
    Snapshot,
    SourceFile,
]:
    api_source, connection, snapshot, source = await _api_source_materials(
        session,
        task_id=task_id,
        tenant_id=tenant_id,
        binding=binding,
        invalid_code="api_authority_artifact_binding_invalid",
    )
    if (
        snapshot.file_hash != source.sha256
        or snapshot.mapping_version != api_source.projection_version
    ):
        raise GraphGuardRejected("api_authority_artifact_binding_invalid")
    return api_source, connection, snapshot, source


async def _api_source_materials(
    session: AsyncSession,
    *,
    task_id: UUID,
    tenant_id: str,
    binding: AgentSourceBinding,
    invalid_code: str,
) -> tuple[
    ApiAuthoritySourceRecord,
    ApiConnectionRecord,
    Snapshot,
    SourceFile,
]:
    if (
        binding.role != "authoritative"
        or binding.connector_kind != "api"
        or binding.configuration_id is None
    ):
        raise GraphGuardRejected("api_authority_binding_invalid")
    try:
        connection_id = UUID(binding.configuration_id)
    except ValueError as error:
        raise GraphGuardRejected("api_authority_binding_invalid") from error
    api_source = await session.scalar(
        select(ApiAuthoritySourceRecord).where(
            ApiAuthoritySourceRecord.task_id == task_id,
            ApiAuthoritySourceRecord.tenant_id == tenant_id,
            ApiAuthoritySourceRecord.connection_id == connection_id,
        )
    )
    connection = await session.scalar(
        select(ApiConnectionRecord).where(
            ApiConnectionRecord.id == connection_id,
            ApiConnectionRecord.tenant_id == tenant_id,
        )
    )
    snapshot, source = await _source_snapshot(
        session,
        task_id=task_id,
        role=binding.role,
    )
    if (
        api_source is None
        or connection is None
        or api_source.state != "ready"
        or api_source.source_file_id != source.id
        or api_source.snapshot_id != snapshot.id
        or api_source.content_sha256 != source.sha256
    ):
        raise GraphGuardRejected(invalid_code)
    return api_source, connection, snapshot, source


def _selected_agent_entities(values: list[str]) -> frozenset[AgentEntityKind]:
    aliases = {
        "department": AgentEntityKind.DEPARTMENT,
        "organization_unit": AgentEntityKind.DEPARTMENT,
        "student": AgentEntityKind.STUDENT,
        "teacher": AgentEntityKind.TEACHER,
    }
    selected = frozenset(aliases[value] for value in values if value in aliases)
    return selected or frozenset(AgentEntityKind)


def _bind_input_marks(
    marks: tuple[AgentInputMark, ...],
    persisted: tuple[AgentInputRecord, ...],
) -> tuple[AgentInputMark, ...]:
    return _bind_marks(
        marks,
        {
            record.raw_row_number: record.id
            for record in persisted
            if record.raw_row_number is not None
        },
        error_message="ingestion mark does not correspond to a persisted input",
    )


def _bind_api_input_marks(
    marks: tuple[AgentInputMark, ...],
    persisted: tuple[AgentInputRecord, ...],
) -> tuple[AgentInputMark, ...]:
    return _bind_marks(
        marks,
        {record.stable_order: record.id for record in persisted},
        error_message="API ingestion mark does not correspond to a persisted input",
    )


def _bind_database_input_marks(
    marks: tuple[AgentInputMark, ...],
    persisted: tuple[AgentInputRecord, ...],
) -> tuple[AgentInputMark, ...]:
    return _bind_marks(
        marks,
        {record.stable_order: record.id for record in persisted},
        error_message="database ingestion mark does not correspond to a persisted input",
    )


def _bind_marks(
    marks: tuple[AgentInputMark, ...],
    input_ids_by_position: dict[int, UUID],
    *,
    error_message: str,
) -> tuple[AgentInputMark, ...]:
    result: list[AgentInputMark] = []
    for mark in marks:
        position = mark.safe_evidence.get("row_number")
        if not isinstance(position, int) or position not in input_ids_by_position:
            raise ValueError(error_message)
        result.append(
            mark.model_copy(update={"input_record_id": input_ids_by_position[position]})
        )
    return tuple(result)


def _safe_csv_problem_code(error: CsvFormatError | AgentContractError) -> str:
    if isinstance(error, CsvFormatError):
        return "csv_format_invalid"
    return "fixed_field_mapping_unresolved"


def _csv_source_profiles(
    materials: dict[str, tuple[Snapshot, SourceFile]],
) -> tuple[
    tuple[CsvSourceSchemaProfile, ...],
    dict[str, dict[str, str]],
]:
    profiles: list[CsvSourceSchemaProfile] = []
    field_refs: dict[str, dict[str, str]] = {}
    mapper = AgentContractMapper()
    for role in ("authoritative", "target"):
        if role not in materials:
            continue
        _snapshot, source = materials[role]
        path = Path(source.storage_path)
        inspection = inspect_csv(path)
        frame = read_csv_frame(path, inspection)
        columns: list[CsvColumnProfile] = []
        role_refs: dict[str, str] = {}
        for index, header in enumerate(inspection.headers):
            field_ref = f"csv-column:{role}:{index}"
            role_refs[field_ref] = header
            values = [
                str(value).strip()
                for value in frame.get_column(header).to_list()
                if value is not None and str(value).strip()
            ]
            total = max(frame.height, 1)
            deterministic = mapper.resolve_header_mapping((header,))
            columns.append(
                CsvColumnProfile(
                    source_field_ref=field_ref,
                    header=header,
                    inferred_type=_infer_csv_column_type(values),
                    empty_ratio=(frame.height - len(values)) / total,
                    unique_ratio=(len(set(values)) / len(values) if values else 0),
                    candidate_contract_fields=tuple(deterministic),
                )
            )
        field_refs[role] = role_refs
        profiles.append(
            CsvSourceSchemaProfile(
                source_role=role,
                columns=tuple(columns),
            )
        )
    return tuple(profiles), field_refs


def _infer_csv_column_type(values: list[str]) -> str:
    if not values:
        return "unknown"
    sample = values[:50]
    if sum("@" in value for value in sample) * 2 >= len(sample):
        return "email"
    compact_digits = [
        "".join(character for character in value if character.isdigit())
        for value in sample
    ]
    if sum(
        len(value) == 11 and value.startswith("1") for value in compact_digits
    ) * 2 >= len(sample):
        return "phone"
    if len(set(sample)) == len(sample):
        return "identifier"
    return "text"


def _deterministic_csv_pair_mapping(
    profiles: tuple[CsvSourceSchemaProfile, ...],
) -> CsvSchemaMappingOutput | None:
    by_role: dict[str, tuple[CsvFieldMapping, ...]] = {}
    mapper = AgentContractMapper()
    for profile in profiles:
        headers = tuple(column.header for column in profile.columns)
        try:
            mapper.assert_recognizable_headers(headers)
            mapping = mapper.resolve_header_mapping(headers)
        except AgentContractError:
            return None
        refs_by_header = {
            column.header: column.source_field_ref for column in profile.columns
        }
        by_role[profile.source_role] = tuple(
            CsvFieldMapping(
                source_field_ref=refs_by_header[header],
                contract_field=contract_field,
                entity_kinds=(
                    ("student",)
                    if contract_field == "class_name"
                    else ("department", "student", "teacher")
                ),
                normalizer_id=_normalizer_for_contract_field(contract_field),
            )
            for contract_field, header in mapping.items()
        )
    return CsvSchemaMappingOutput(
        schema_version="fixed-six-field-mapping-v2",
        authoritative_mappings=by_role["authoritative"],
        target_mappings=by_role["target"],
        unresolved_required_fields=(),
    )


def _normalizer_for_contract_field(field: str) -> str:
    return {
        "category": "normalize_category",
        "name": "trim_text",
        "number": "trim_identifier",
        "class_name": "trim_text",
        "phone": "normalize_phone",
        "email": "normalize_email",
    }[field]


def _validate_csv_mapping_output(
    output: object,
    *,
    field_refs: dict[str, dict[str, str]],
    roles: tuple[str, ...] = ("authoritative", "target"),
) -> CsvSchemaMappingOutput:
    if not isinstance(output, CsvSchemaMappingOutput):
        raise ValueError("CSV mapping Skill returned another schema")
    mappings_by_role = {
        "authoritative": output.authoritative_mappings,
        "target": output.target_mappings,
    }
    for role in {"authoritative", "target"} - set(roles):
        if mappings_by_role[role]:
            raise ValueError(f"{role} CSV mapping was not requested")
    for role in roles:
        mappings = mappings_by_role[role]
        contract_fields = [mapping.contract_field for mapping in mappings]
        source_refs = [mapping.source_field_ref for mapping in mappings]
        if len(set(contract_fields)) != len(contract_fields):
            raise ValueError(f"{role} CSV mapping repeats a contract field")
        if len(set(source_refs)) != len(source_refs):
            raise ValueError(f"{role} CSV mapping repeats a source field")
        for mapping in mappings:
            if mapping.source_field_ref not in field_refs[role]:
                raise ValueError(
                    f"{role} CSV mapping references an unknown source field"
                )
            if mapping.normalizer_id != _normalizer_for_contract_field(
                mapping.contract_field
            ):
                raise ValueError(f"{role} CSV mapping uses an invalid normalizer")
            if mapping.contract_field == "class_name" and mapping.entity_kinds != (
                "student",
            ):
                raise ValueError("class_name CSV mapping only applies to students")
    allowed_unresolved = {
        f"{role}.{field}"
        for role in roles
        for field in ("category", "name", "number", "class_name", "phone", "email")
    }
    unresolved = set(output.unresolved_required_fields)
    if len(unresolved) != len(output.unresolved_required_fields):
        raise ValueError("CSV mapping repeats an unresolved field")
    if not unresolved <= allowed_unresolved:
        raise ValueError("CSV mapping returned an unknown unresolved field")
    for role in roles:
        mappings = mappings_by_role[role]
        mapped_fields = {mapping.contract_field for mapping in mappings}
        unresolved_fields = {
            item.removeprefix(f"{role}.")
            for item in unresolved
            if item.startswith(f"{role}.")
        }
        if mapped_fields & unresolved_fields:
            raise ValueError(f"{role} CSV mapping marks mapped fields unresolved")
        if mapped_fields | unresolved_fields != _fixed_contract_fields():
            raise ValueError(
                f"{role} CSV mapping omitted fields without marking unresolved"
            )
    return output


def _csv_mapping_checkpoint_payload(
    output: CsvSchemaMappingOutput,
    *,
    field_refs: dict[str, dict[str, str]],
    model_calls: int,
) -> dict[str, object]:
    mappings: dict[str, dict[str, str]] = {}
    for role, items in (
        ("authoritative", output.authoritative_mappings),
        ("target", output.target_mappings),
    ):
        mappings[role] = {
            item.contract_field: field_refs[role][item.source_field_ref]
            for item in items
        }
    return {
        "schema_version": output.schema_version,
        "resolved": not output.unresolved_required_fields,
        "mappings": mappings,
        "unresolved_required_fields": list(output.unresolved_required_fields),
        "model_calls": model_calls,
    }


def _csv_mapping_input_hash(
    materials: dict[str, tuple[Snapshot, SourceFile]],
) -> str:
    return _hash(
        {
            role: {
                "snapshot_id": str(snapshot.id),
                "source_hash": source.sha256,
            }
            for role, (snapshot, source) in materials.items()
        }
    )


def _fixed_contract_fields() -> set[str]:
    return {
        "category",
        "name",
        "number",
        "class_name",
        "phone",
        "email",
    }


def _infer_database_column_type(column_name: str) -> str:
    candidates = _database_contract_candidates(column_name)
    if "phone" in candidates:
        return "phone"
    if "email" in candidates:
        return "email"
    if "number" in candidates:
        return "identifier"
    if candidates:
        return "text"
    return "unknown"


def _database_contract_candidates(column_name: str) -> tuple[str, ...]:
    try:
        mapping = AgentContractMapper().resolve_header_mapping((column_name,))
    except AgentContractError:
        return ()
    return tuple(mapping)


def _database_mapping_output_from_config(
    *,
    configured_mappings: dict[str, dict[str, str]],
    field_refs: dict[str, dict[str, str]],
    schema_version: str = "fixed-six-field-sql-mapping-v2",
) -> DatabaseSchemaMappingOutput:
    by_role: dict[str, tuple[DatabaseFieldMapping, ...]] = {
        "authoritative": (),
        "target": (),
    }
    for role in field_refs:
        refs_by_column = {
            column: field_ref for field_ref, column in field_refs[role].items()
        }
        by_role[role] = tuple(
            DatabaseFieldMapping(
                source_field_ref=refs_by_column[column],
                contract_field=field,
                entity_kinds=_entity_kinds_for_contract_field(field),
                normalizer_id=_normalizer_for_contract_field(field),
            )
            for field, column in configured_mappings[role].items()
        )
    return DatabaseSchemaMappingOutput(
        schema_version=schema_version,
        authoritative_mappings=by_role["authoritative"],
        target_mappings=by_role["target"],
        unresolved_required_fields=(),
    )


class _DatabaseMappingContractViolation(ValueError):
    def __init__(self, code: str, *, path: str = "$") -> None:
        super().__init__("database mapping violated its fixed contract")
        self.repair_feedback = ({"path": path, "code": code},)


def _database_mapping_llm_roles(
    materials: _DatabaseMappingMaterials,
    *,
    mapping_schema_version: str,
) -> frozenset[str]:
    if mapping_schema_version == "fixed-six-field-sql-mapping-v3":
        return frozenset(
            role for role, mode in materials.mapping_modes.items() if mode == "llm"
        )
    fixed_fields = _fixed_contract_fields()
    return frozenset(
        role
        for role, mapping in materials.configured_mappings.items()
        if set(mapping) != fixed_fields
    )


def _merge_database_mapping_roles(
    output: object,
    *,
    configured_output: DatabaseSchemaMappingOutput,
    llm_roles: frozenset[str],
) -> DatabaseSchemaMappingOutput:
    if not isinstance(output, DatabaseSchemaMappingOutput):
        raise _DatabaseMappingContractViolation("output_schema_invalid")
    model_mappings = {
        "authoritative": output.authoritative_mappings,
        "target": output.target_mappings,
    }
    configured_mappings = {
        "authoritative": configured_output.authoritative_mappings,
        "target": configured_output.target_mappings,
    }
    for role in ("authoritative", "target"):
        if role not in llm_roles and model_mappings[role]:
            raise _DatabaseMappingContractViolation(
                "role_not_requested",
                path=f"{role}_mappings",
            )
    if any(
        unresolved.split(".", maxsplit=1)[0] not in llm_roles
        for unresolved in output.unresolved_required_fields
    ):
        raise _DatabaseMappingContractViolation(
            "role_not_requested",
            path="unresolved_required_fields",
        )
    return DatabaseSchemaMappingOutput(
        schema_version=output.schema_version,
        authoritative_mappings=(
            model_mappings["authoritative"]
            if "authoritative" in llm_roles
            else configured_mappings["authoritative"]
        ),
        target_mappings=(
            model_mappings["target"]
            if "target" in llm_roles
            else configured_mappings["target"]
        ),
        unresolved_required_fields=output.unresolved_required_fields,
    )


def _validate_database_mapping_output(
    output: object,
    *,
    field_refs: dict[str, dict[str, str]],
    configured_mappings: dict[str, dict[str, str]],
    enforce_configured_roles: frozenset[str],
    forbidden_source_refs: dict[str, frozenset[str]],
    expected_schema_version: str,
) -> DatabaseSchemaMappingOutput:
    if not isinstance(output, DatabaseSchemaMappingOutput):
        raise _DatabaseMappingContractViolation("output_schema_invalid")
    if output.schema_version != expected_schema_version:
        raise _DatabaseMappingContractViolation(
            "contract_version_mismatch",
            path="schema_version",
        )
    active_roles = tuple(
        role for role in ("authoritative", "target") if role in field_refs
    )
    unresolved = set(output.unresolved_required_fields)
    allowed_unresolved = {
        f"{role}.{field}"
        for role in active_roles
        for field in _fixed_contract_fields()
    }
    if not unresolved <= allowed_unresolved:
        raise _DatabaseMappingContractViolation(
            "unresolved_field_unknown",
            path="unresolved_required_fields",
        )

    compiled: dict[str, dict[str, str]] = {}
    mappings_by_role = {
        "authoritative": output.authoritative_mappings,
        "target": output.target_mappings,
    }
    for role in ("authoritative", "target"):
        mappings = mappings_by_role[role]
        if role not in active_roles:
            if mappings:
                raise _DatabaseMappingContractViolation(
                    "role_not_requested",
                    path=f"{role}_mappings",
                )
            continue
        contract_fields = [mapping.contract_field for mapping in mappings]
        source_refs = [mapping.source_field_ref for mapping in mappings]
        if len(set(contract_fields)) != len(contract_fields):
            raise _DatabaseMappingContractViolation(
                "contract_field_duplicated",
                path=f"{role}_mappings",
            )
        if len(set(source_refs)) != len(source_refs):
            raise _DatabaseMappingContractViolation(
                "source_field_duplicated",
                path=f"{role}_mappings",
            )
        compiled[role] = {}
        for mapping in mappings:
            mapping_path = f"{role}_mappings.{mapping.contract_field}"
            if mapping.source_field_ref not in field_refs[role]:
                raise _DatabaseMappingContractViolation(
                    "source_field_unknown",
                    path=mapping_path,
                )
            if mapping.source_field_ref in forbidden_source_refs[role]:
                raise _DatabaseMappingContractViolation(
                    "primary_or_version_field_forbidden",
                    path=mapping_path,
                )
            if mapping.normalizer_id != _normalizer_for_contract_field(
                mapping.contract_field
            ):
                raise _DatabaseMappingContractViolation(
                    "normalizer_invalid",
                    path=mapping_path,
                )
            if mapping.entity_kinds != _entity_kinds_for_contract_field(
                mapping.contract_field
            ):
                raise _DatabaseMappingContractViolation(
                    "entity_kinds_invalid",
                    path=mapping_path,
                )
            compiled[role][mapping.contract_field] = field_refs[role][
                mapping.source_field_ref
            ]
        mapped_fields = set(compiled[role])
        unresolved_fields = {
            item.removeprefix(f"{role}.")
            for item in unresolved
            if item.startswith(f"{role}.")
        }
        if mapped_fields & unresolved_fields:
            raise _DatabaseMappingContractViolation(
                "mapped_and_unresolved_conflict",
                path=f"{role}_mappings",
            )
        if mapped_fields | unresolved_fields != _fixed_contract_fields():
            raise _DatabaseMappingContractViolation(
                "fixed_field_coverage_incomplete",
                path=f"{role}_mappings",
            )
        if (
            role in enforce_configured_roles
            and compiled[role] != configured_mappings[role]
        ):
            raise _DatabaseMappingContractViolation(
                "explicit_mapping_mismatch",
                path=f"{role}_mappings",
            )
    return output


def _database_mapping_cache_coordinates(
    materials: _DatabaseMappingMaterials,
) -> tuple[str, str, str, str]:
    connector_ids = {
        role: materials.connector_ids.get(role, f"non-database:{role}")
        for role in ("authoritative", "target")
    }
    schema_fingerprints = {
        role: materials.schema_fingerprints.get(
            role,
            _hash({"source_role": role, "connector_kind": "non-database"}),
        )
        for role in ("authoritative", "target")
    }
    return (
        connector_ids["authoritative"],
        connector_ids["target"],
        schema_fingerprints["authoritative"],
        schema_fingerprints["target"],
    )


def _entity_kinds_for_contract_field(field: str) -> tuple[str, ...]:
    if field == "class_name":
        return ("student",)
    return ("department", "student", "teacher")


def _database_mapping_checkpoint_payload(
    output: DatabaseSchemaMappingOutput,
    *,
    field_refs: dict[str, dict[str, str]],
    schema_fingerprints: dict[str, str],
    source_versions: dict[str, str],
    model_calls: int,
    cache_hit: bool = False,
) -> dict[str, object]:
    mappings: dict[str, dict[str, str]] = {}
    for role, items in (
        ("authoritative", output.authoritative_mappings),
        ("target", output.target_mappings),
    ):
        mappings[role] = {
            item.contract_field: field_refs[role][item.source_field_ref]
            for item in items
        }
    return {
        "schema_version": output.schema_version,
        "resolved": not output.unresolved_required_fields,
        "mappings": mappings,
        "schema_fingerprints": schema_fingerprints,
        "source_versions": source_versions,
        "unresolved_required_fields": list(output.unresolved_required_fields),
        "model_calls": model_calls,
        "cache_hit": cache_hit,
    }


def _database_mapping_role_checkpoint_payload(
    output: DatabaseSchemaMappingOutput,
    *,
    role: str,
    connector_id: str,
    field_refs: dict[str, dict[str, str]],
    schema_fingerprint: str,
    source_version: str,
    model_calls: int,
    cache_hit: bool,
) -> dict[str, object]:
    items = (
        output.authoritative_mappings
        if role == "authoritative"
        else output.target_mappings
    )
    mapping = {
        item.contract_field: field_refs[role][item.source_field_ref] for item in items
    }
    unresolved = [
        item
        for item in output.unresolved_required_fields
        if item.startswith(f"{role}.")
    ]
    return {
        "schema_version": "source-ingestion-v3",
        "mapping_version": output.schema_version,
        "source_role": role,
        "connector_kind": "database",
        "connector_id": connector_id,
        "resolved": not unresolved and set(mapping) == _fixed_contract_fields(),
        "mapping": mapping,
        "schema_fingerprint": schema_fingerprint,
        "source_version": source_version,
        "unresolved_required_fields": unresolved,
        "model_calls": model_calls,
        "cache_hit": cache_hit,
    }


async def _find_exact_batch(
    session: AsyncSession,
    *,
    run_id: UUID,
    work_ids: tuple[UUID, ...],
) -> AgentModelBatchRecord:
    batches = tuple(
        await session.scalars(
            select(AgentModelBatchRecord).where(
                AgentModelBatchRecord.run_id == run_id,
                AgentModelBatchRecord.status.in_(("pending", "claimed", "completed")),
            )
        )
    )
    for batch in batches:
        members = tuple(
            await session.scalars(
                select(AgentModelBatchItemRecord.work_item_id)
                .where(AgentModelBatchItemRecord.batch_id == batch.id)
                .order_by(AgentModelBatchItemRecord.ordinal)
            )
        )
        if members == work_ids:
            return batch
    raise LookupError("Agent graph analysis batch membership is missing")


async def _work_rows(
    session: AsyncSession,
    work_ids: tuple[UUID, ...],
) -> tuple[tuple[AgentWorkItemRecord, AgentInputRecord], ...]:
    by_id = {
        work.id: (work, record)
        for work, record in tuple(
            await session.execute(
                select(AgentWorkItemRecord, AgentInputRecord)
                .join(
                    AgentInputRecord,
                    AgentInputRecord.id == AgentWorkItemRecord.subject_input_id,
                )
                .where(AgentWorkItemRecord.id.in_(work_ids))
            )
        )
    }
    if set(by_id) != set(work_ids):
        raise LookupError("Agent graph work item evidence is incomplete")
    return tuple(by_id[work_id] for work_id in work_ids)


def _termination_report_context(
    context: GraphWorkContext,
    facts: Mapping[str, object],
) -> dict[str, object]:
    findings = facts.get("findings")
    mutations = facts.get("mutations")
    finding_rows = findings if isinstance(findings, list) else []
    mutation_rows = mutations if isinstance(mutations, list) else []
    succeeded = [
        item
        for item in mutation_rows
        if isinstance(item, Mapping) and item.get("status") == "succeeded"
    ]
    verified = [
        item
        for item in succeeded
        if isinstance(item.get("verification"), Mapping)
        and item["verification"].get("valid") is True
    ]
    latest_failure = facts.get("latest_failure")
    analysis_progress = facts.get("analysis_progress")
    has_system_failure = isinstance(latest_failure, Mapping)
    result: dict[str, object] = {
        "reason_code": (
            "system_failure_then_operator_terminated"
            if has_system_failure
            else "operator_requested"
        ),
        "reason_zh": (
            "系统处理失败后由操作人终止任务"
            if has_system_failure
            else "操作人主动终止任务"
        ),
        "current_node": context.current_node,
        "phase_zh": "报告生成",
        "recorded_finding_count": len(finding_rows),
        "succeeded_mutation_count": len(succeeded),
        "verified_mutation_count": len(verified),
        "data_modified": bool(succeeded),
    }
    if isinstance(latest_failure, Mapping) and isinstance(
        analysis_progress, Mapping
    ):
        result.update(
            {
                "failure_code": str(latest_failure.get("code", ""))[:128],
                "failure_phase": str(latest_failure.get("phase", ""))[:64],
                "failure_categories": list(
                    latest_failure.get("failure_categories", [])
                )[:20],
                "analysis_progress": dict(analysis_progress),
            }
        )
    return result


def _termination_report_summary(context: Mapping[str, object]) -> str:
    def report_count(key: str) -> int:
        value = context.get(key)
        return value if isinstance(value, int) else 0

    finding_count = report_count("recorded_finding_count")
    succeeded_count = report_count("succeeded_mutation_count")
    verified_count = report_count("verified_mutation_count")
    if context.get("reason_code") == "system_failure_then_operator_terminated":
        progress = context.get("analysis_progress")
        completed_batches = (
            progress.get("completed_batch_count", 0)
            if isinstance(progress, Mapping)
            else 0
        )
        total_batches = (
            progress.get("total_batch_count", 0)
            if isinstance(progress, Mapping)
            else 0
        )
        return (
            f"任务先因系统处理失败而安全暂停，随后由操作人终止；"
            f"终止前已完成 {completed_batches}/{total_batches} 个分析批次，"
            "已完成事实仍被保留，未完成批次没有写入治理结论。"
        )
    if finding_count == 0 and succeeded_count == 0:
        return (
            "任务已按操作人要求终止；终止前尚未形成治理问题，"
            "也没有修改目标数据。"
        )
    return (
        f"任务已按操作人要求终止；终止前记录了 {finding_count} 项治理问题，"
        f"完成 {succeeded_count} 项数据修改，其中 {verified_count} 项通过服务端验证。"
    )


def _deterministic_failure_analysis(
    context: Mapping[str, object],
) -> dict[str, str]:
    failure_code = str(context.get("failure_code") or "system_failure")
    return {
        "reason_code": "system_failure_then_operator_terminated",
        "title_zh": "系统处理失败后终止报告",
        "summary_zh": _termination_report_summary(context),
        "impact_zh": (
            "已完成的分析事实和审计记录继续保留；未完成批次没有生成可执行治理结论。"
        ),
        "suggestion_zh": (
            f"请按安全错误码 {failure_code} 检查模型服务、网络或输出合同后重新运行任务。"
        ),
    }


def _deterministic_input_exception_analyses(
    facts: Mapping[str, object],
) -> list[dict[str, str]]:
    diagnostics = facts.get("input_diagnostics")
    reason_counts = (
        diagnostics.get("reason_counts")
        if isinstance(diagnostics, Mapping)
        else None
    )
    if not isinstance(reason_counts, Mapping):
        return []
    analyses: list[dict[str, str]] = []
    for reason_code, raw_count in sorted(reason_counts.items()):
        if not isinstance(raw_count, int) or raw_count <= 0:
            continue
        code = str(reason_code)
        if code == "authority_identity_absent":
            analyses.append(
                {
                    "reason_code": code,
                    "title_zh": "权威数据缺少可用身份标识",
                    "analysis_zh": (
                        f"权威数据中有 {raw_count} 条记录缺少编号、电话或邮箱等"
                        "可用身份标识。"
                    ),
                    "impact_zh": (
                        "这些记录无法可靠匹配，系统已阻止其进入自动治理。"
                    ),
                    "suggestion_zh": (
                        "请补充稳定的编号、电话或邮箱后重新运行任务。"
                    ),
                }
            )
            continue
        analyses.append(
            {
                "reason_code": code,
                "title_zh": "输入数据不符合治理要求",
                "analysis_zh": f"有 {raw_count} 条输入记录触发异常规则 {code}。",
                "impact_zh": "这些记录未进入自动治理，以避免产生不可靠的数据修改。",
                "suggestion_zh": "请根据异常规则修正源数据后重新运行任务。",
            }
        )
    return analyses


def _outcome(action: AllowedActionV1) -> GraphActionOutcome:
    return GraphActionOutcome(
        action_id=action.action_id,
        evidence_refs=action.required_evidence,
    )


def _fallback_analysis_action(
    action: AllowedActionV1,
    work_item_ids: tuple[UUID, ...],
) -> AllowedActionV1:
    digest = hashlib.sha256(
        ",".join(str(work_item_id) for work_item_id in work_item_ids).encode()
    ).hexdigest()[:16]
    return action.model_copy(
        update={
            "action_id": f"fallback_analysis_{digest}",
            "resource_ids": tuple(
                f"work-item:{work_item_id}" for work_item_id in work_item_ids
            ),
            "required_evidence": tuple(
                f"paired-record:{work_item_id}" for work_item_id in work_item_ids
            ),
        }
    )


def _task_uses_database(task: ReconciliationTask | None) -> bool:
    if task is None or not isinstance(task.agent_intent, dict):
        return False
    for key in ("source", "target"):
        selection = task.agent_intent.get(key)
        if isinstance(selection, dict) and selection.get("kind") == "database":
            return True
    return task.agent_intent.get("source_mode") == "database"


def _task_uses_remote_csv(task: ReconciliationTask | None) -> bool:
    if task is None or not isinstance(task.agent_intent, dict):
        return False
    source = task.agent_intent.get("source")
    return isinstance(source, dict) and source.get("kind") == "remote_csv"


def _legacy_context(
    context: GraphWorkContext,
    phase: AgentPhase,
) -> AgentWorkContext:
    return AgentWorkContext(
        worker_id=context.worker_id,
        run_id=context.run_id,
        task_id=context.task_id,
        tenant_id=context.tenant_id,
        phase=phase,
        attempt_count=context.attempt_count,
        lease_token=context.lease_token,
    )


def _operation_status(
    status: str,
) -> str:
    if status in {
        "succeeded",
        "already_restored",
        "conflict_skipped",
        "failed",
        "blocked",
    }:
        return status
    return "skipped"


def _source_role(resource_id: str) -> str:
    parts = resource_id.split(":")
    if (
        len(parts) not in {3, 4}
        or parts[0] != "source"
        or parts[1]
        not in {
            "authoritative",
            "target",
        }
    ):
        raise ValueError("source resource is invalid")
    return parts[1]


def _source_page(resource_id: str) -> int:
    parts = resource_id.split(":")
    if len(parts) != 4 or parts[2] != "page" or not parts[3].isdecimal():
        raise ValueError("source page resource is invalid")
    return int(parts[3])


def _resource_uuid(value: str, prefix: str) -> UUID:
    marker = f"{prefix}:"
    if not value.startswith(marker):
        raise ValueError(f"{prefix} resource is invalid")
    return UUID(value.removeprefix(marker))


def _only(values: tuple[str, ...]) -> str:
    if len(values) != 1:
        raise ValueError("graph action requires exactly one resource")
    return values[0]


def _hash(value: object) -> str:
    return f"sha256:{_raw_hash(value)}"


def _raw_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()

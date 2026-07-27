"""Production action execution for ``agent-graph-v1`` CSV tasks."""

import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_graph.analysis_executors import (
    GraphAnalysisResultWriter,
    GraphIngestionAnalysisExecutors,
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
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.reconciliation.agent_identity import AgentIdentityIndexBuilder
from app.repositories.agent_analysis import AgentAnalysisRepository
from app.repositories.executions import ExecutionRepository
from app.schemas.agent_ingestion import AgentEntityKind, AgentInputMark, AgentSourceRole


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
            context.current_node in {"execute_ready_operations", "execute_remaining_independent"}
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

    async def _inspect_source(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
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

    async def _inspect_database_source_v2(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        role = _source_role(_only(action.resource_ids))
        connector_id = await self._database_connector_id(context.task_id, role)
        connector = await self._database_connector(connector_id)
        health = await connector.health()
        schema = await connector.discover_schema()
        version = await connector.version()
        configuration = connector.configuration
        if not isinstance(configuration, DatabaseConnectorConfiguration):
            raise TypeError("SQL task resolved a non-database connector")
        physical_fields = set(schema.fields)
        configured_fields = set(configuration.field_columns.values())
        missing_contract_fields = _fixed_contract_fields().difference(configuration.field_columns)
        problem_codes: list[str] = []
        if not health.ready:
            problem_codes.append("database_connector_unavailable")
        if not configured_fields <= physical_fields:
            problem_codes.append("database_schema_mapping_stale")
        mapping_required = bool(missing_contract_fields) and not problem_codes
        payload = {
            "schema_version": "source-ingestion-v2",
            "mapping_version": "fixed-six-field-sql-mapping-v2",
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
        if context.ingestion_contract_version == "source-ingestion-v2":
            if action.resource_ids == ("source-pair:current",):
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
                    raise ValueError("normalization action points to an empty source page")
                tools, runner, manifest_id = await self._analysis_runtime(
                    session,
                    context=context,
                    action=action,
                )
                result = await GraphIngestionAnalysisExecutors(runner).normalize_input_batch(
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

    async def _resolve_database_mapping_v2(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        profiles: list[DatabaseSourceSchemaProfile] = []
        field_refs: dict[str, dict[str, str]] = {}
        configured_mappings: dict[str, dict[str, str]] = {}
        connector_ids: dict[str, str] = {}
        schema_fingerprints: dict[str, str] = {}
        source_versions: dict[str, str] = {}
        for role in ("authoritative", "target"):
            connector_id = await self._database_connector_id(context.task_id, role)
            connector_ids[role] = connector_id
            connector = await self._database_connector(connector_id)
            schema = await connector.discover_schema()
            configuration = connector.configuration
            if not isinstance(configuration, DatabaseConnectorConfiguration):
                raise TypeError("SQL task resolved a non-database connector")
            missing = set(configuration.field_columns.values()).difference(schema.fields)
            if missing:
                raise ValueError(f"{role} database mapping references unavailable fields")
            schema_fields = tuple(sorted(schema.fields))
            role_refs = {
                f"database-column:{role}:{index}": field
                for index, field in enumerate(schema_fields)
            }
            field_refs[role] = role_refs
            configured_mappings[role] = dict(configuration.field_columns)
            profiles.append(
                DatabaseSourceSchemaProfile(
                    source_role=role,
                    connector_id=connector_id,
                    dialect=configuration.dialect,
                    relation_ref=(f"database-relation:{role}:{configuration.table_name}"),
                    stable_key_ref=next(
                        field_ref
                        for field_ref, field in role_refs.items()
                        if field == configuration.primary_key
                    ),
                    columns=tuple(
                        DatabaseColumnProfile(
                            source_field_ref=field_ref,
                            column_name=field,
                            inferred_type=_infer_database_column_type(field),
                            nullable=field in schema.nullable_fields,
                            candidate_contract_fields=(_database_contract_candidates(field)),
                        )
                        for field_ref, field in role_refs.items()
                    ),
                )
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

        fixed_fields = _fixed_contract_fields()
        deterministic = all(
            set(mapping) == fixed_fields for mapping in configured_mappings.values()
        )
        async with self._session_factory() as session:
            async with session.begin():
                output: DatabaseSchemaMappingOutput
                cache_hit = False
                if deterministic:
                    output = _database_mapping_output_from_config(
                        configured_mappings=configured_mappings,
                        field_refs=field_refs,
                    )
                    model_calls = 0
                else:
                    runtime = AgentRuntimeRepository(session)
                    cached = await runtime.get_database_schema_mapping(
                        tenant_id=context.tenant_id,
                        authoritative_connector_id=connector_ids["authoritative"],
                        target_connector_id=connector_ids["target"],
                        authoritative_schema_fingerprint=schema_fingerprints["authoritative"],
                        target_schema_fingerprint=schema_fingerprints["target"],
                        ingestion_contract_version=context.ingestion_contract_version,
                        skill_name="understand-organization-database-schema",
                        skill_version="1.0.0",
                    )
                    if cached is not None:
                        if _hash(cached.mapping) != cached.content_hash:
                            raise ValueError("database schema mapping cache failed integrity check")
                        output = _validate_database_mapping_output(
                            DatabaseSchemaMappingOutput.model_validate(cached.mapping),
                            field_refs=field_refs,
                            configured_target_mapping=configured_mappings["target"],
                        )
                        model_calls = 0
                        cache_hit = True
                    else:
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
                                skill_name="understand-organization-database-schema",
                                skill_version="1.0.0",
                                input_payload=DatabaseSchemaMappingInput(
                                    task_id=context.task_id,
                                    run_id=context.run_id,
                                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                                    evidence_refs=action.required_evidence,
                                    sources=(profiles[0], profiles[1]),
                                ).model_dump(mode="json"),
                            ),
                            result_validator=lambda candidate: _validate_database_mapping_output(
                                candidate,
                                field_refs=field_refs,
                                configured_target_mapping=configured_mappings["target"],
                            ),
                        )
                        validated_output = result.output
                        if not isinstance(
                            validated_output,
                            DatabaseSchemaMappingOutput,
                        ):
                            raise RuntimeError("validated database mapping output changed type")
                        output = validated_output
                        model_calls = result.attempt_count
                        if not output.unresolved_required_fields:
                            mapping = output.model_dump(mode="json")
                            await runtime.save_database_schema_mapping(
                                tenant_id=context.tenant_id,
                                authoritative_connector_id=connector_ids["authoritative"],
                                target_connector_id=connector_ids["target"],
                                authoritative_schema_fingerprint=(
                                    schema_fingerprints["authoritative"]
                                ),
                                target_schema_fingerprint=schema_fingerprints["target"],
                                ingestion_contract_version=context.ingestion_contract_version,
                                skill_name="understand-organization-database-schema",
                                skill_version="1.0.0",
                                mapping=mapping,
                                content_hash=_hash(mapping),
                            )
                payload = _database_mapping_checkpoint_payload(
                    output,
                    field_refs=field_refs,
                    schema_fingerprints=schema_fingerprints,
                    source_versions=source_versions,
                    model_calls=model_calls,
                    cache_hit=cache_hit,
                )
                await AgentRuntimeRepository(session).save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key="graph-database-field-mapping-v2",
                    input_hash=_hash(
                        {
                            "schema_fingerprints": schema_fingerprints,
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
            snapshot, _source = await _source_snapshot(
                session,
                task_id=context.task_id,
                role=role,
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
            raise ConnectorConflictError("database source changed during bounded extraction")
        async with self._session_factory() as session:
            async with session.begin():
                repository = AgentAnalysisRepository(session)
                persisted = await repository.persist_inputs(outcome.records)
                marks = _bind_input_marks(outcome.marks, persisted)
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
                            storage_path=(f"database://{connector_id}/version/{version_hash}"),
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

    async def _database_connector_id(self, task_id: UUID, role: str) -> str:
        async with self._session_factory() as session:
            task = await session.get(ReconciliationTask, task_id)
            if task is None or not isinstance(task.agent_intent, dict):
                raise LookupError("SQL Agent task intent is missing")
            selection = task.agent_intent.get("source" if role == "authoritative" else "target")
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
                mapping_checkpoint = await AgentRuntimeRepository(session).get_checkpoint(
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
                    role_mapping = mappings.get(role) if isinstance(mappings, dict) else None
                    if not isinstance(role_mapping, dict):
                        raise ValueError("CSV fixed-field mapping is missing a source role")
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
                batches = await AgentBatchPlanner(session).create_for_run(run_id=context.run_id)
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
        work_ids = tuple(_resource_uuid(value, "work-item") for value in action.resource_ids)
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
                    work.id: await tools.paired_record_evidence(f"paired-record:{work.id}")
                    for work, _record in work_rows
                }
                input_payload = ReconcileEntityBatchInput(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    phase=AgentPhase.ANALYZE_BATCHES,
                    evidence_refs=action.required_evidence,
                    work_items=tuple(
                        IdentityWorkItem(
                            work_item_id=work.id,
                            entity_kind=record.entity_kind,
                            target_locator=record.stable_locator,
                            candidate_evidence_refs=(f"paired-record:{work.id}",),
                            paired_evidence=paired_evidence[work.id],
                        )
                        for work, record in work_rows
                    ),
                ).model_dump(mode="json")

        result = None
        model_failure: GraphSubAgentFailure | None = None
        async with self._session_factory() as model_session:
            async with model_session.begin():
                tools, runner, replay_manifest_id = await self._analysis_runtime(
                    model_session,
                    context=context,
                    action=action,
                )
                if replay_manifest_id != manifest_id:
                    raise RuntimeError("analysis evidence manifest replay changed identity")
                try:
                    result = await GraphIngestionAnalysisExecutors(runner).analyze_actionable_batch(
                        GraphSkillInvocation(
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
                        ),
                        expected_work_item_kinds=expected_kinds,
                        allowed_evidence_refs=frozenset(action.required_evidence),
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
                            AgentClarificationRecord.status.in_(("pending", "interpreted")),
                        )
                        .order_by(
                            AgentClarificationRecord.created_at,
                            AgentClarificationRecord.id,
                        )
                    )
                )
                if not clarifications:
                    raise ValueError("identity conflict action has no unresolved clarification")
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
                        "operation_count": len(plan.operations) if plan is not None else 0,
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
                current = await ExecutionRepository(session).current_target_version(context.task_id)
                task = await session.get(ReconciliationTask, context.task_id)
                external_version_hash: str | None = None
                if _task_uses_database(task):
                    if task is None or not isinstance(task.agent_intent, dict):
                        raise LookupError("SQL Agent task intent is missing")
                    target = task.agent_intent.get("target")
                    connector_id = (
                        target.get("configuration_id") if isinstance(target, dict) else None
                    )
                    if not isinstance(connector_id, str):
                        raise ValueError("SQL Agent target connector ID is missing")
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
                expected = "request_cross_phase_replan" if stale else "execute_ready_operations"
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
                                    f"sha256:{current.file_sha256}" if current is not None else None
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
                if not database_task and not self._csv_execution_enabled:
                    raise RuntimeError(
                        "Agent graph CSV execution is disabled before a writable plan"
                    )
                if database_task and self._sql_governance is None:
                    raise RuntimeError(
                        "Agent graph SQL execution is disabled before a writable plan"
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
                            f"verification:{record.id}" if status == "succeeded" else None
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
                        [await execute_operation(operation_id) for operation_id in operation_ids]
                    )
                    await self._record_deterministic_invocation(
                        session,
                        context=context,
                        action=action,
                        output={
                            "plan_id": str(plan.id),
                            "outcomes": [outcome.model_dump(mode="json") for outcome in outcomes],
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
                            UUID(str(output_version_id)) if output_version_id is not None else None
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
                mutations = facts.get("mutations", [])
                rollback_eligible = terminal_state == "completed" and any(
                    isinstance(item, dict) and item.get("status") == "succeeded"
                    for item in mutations
                    if isinstance(mutations, list)
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
                    if terminal_state != "terminated":
                        raise
                    from app.agent_reporting.service import AgentReportingService

                    await AgentReportingService(session).generate(
                        task_id=context.task_id,
                        tenant_id=context.tenant_id,
                        kind="sync",
                        terminal_state=terminal_state,
                        facts=facts,
                        narrative={
                            "title_zh": "任务终止报告",
                            "summary_zh": (
                                "任务已按操作人要求终止。模型报告暂不可用，"
                                "本报告仅保留服务端已验证事实。"
                            ),
                            "fact_refs": [fact_ref],
                            "degraded": True,
                        },
                        generated_by="agent-graph-termination-fallback-v1",
                    )
                    await AgentRuntimeRepository(session).append_event(
                        context.run_id,
                        "termination.report.fallback",
                        {
                            "phase": AgentPhase.GENERATE_REPORT.value,
                            "status": "terminating",
                            "safe_error_code": "termination_report_model_unavailable",
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
                mutations = tuple(dict(item) for item in task.agent_intent.get("operations", []))
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
                        member_ids=tuple(str(item) for item in assessment.conflict_operation_ids),
                        content_hash=_hash(
                            {
                                "conflict_operation_ids": [
                                    str(item) for item in assessment.conflict_operation_ids
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
                approval = tuple(gate for gate in gates if gate.gate_kind == "rollback_approval")
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
                    raise RuntimeError(
                        "SQL rollback connector runtime is unavailable"
                    )
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
                    "output_target_version_id": output_fact[
                        "output_target_version_id"
                    ],
                    "output_target_path": output_fact[
                        "output_target_path"
                    ],
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
        if (
            context.execution_contract_version
            == "deterministic-execution-v2"
        ):
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
                facts = dict(checkpoint.payload) if checkpoint is not None else {"mutations": []}
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
                            UUID(str(output_version_id)) if output_version_id is not None else None
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
                    max_retries=self._max_retries,
                )
                rollback_eligible = any(
                    item.get("status") == "succeeded" for item in facts.get("mutations", [])
                )
                await GraphReportExecutor(session, runner=runner).generate(
                    GraphSkillInvocation(
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
                            outcome="completed",
                            fact_refs=(fact_ref,),
                        ).model_dump(mode="json"),
                    ),
                    tenant_id=context.tenant_id,
                    kind="rollback",
                    terminal_state="completed",
                    facts=facts,
                    expected_rollback_eligible=rollback_eligible,
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
                    output={"guarded_action": action.graph_action_kind or action.action_id},
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
        issued_sensitive_tokens = await tools.prepare_manifest_tokens(action.resource_ids)
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
    current_target = await ExecutionRepository(session).current_target_version(context.task_id)
    target_version = (
        f"sha256:{current_target.file_sha256}"
        if current_target is not None
        else (f"sha256:{target_snapshot.file_hash}" if target_snapshot is not None else None)
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
        f"csv:{int(row['_row_number'])}" for row in frame.slice((page - 1) * 50, 50).to_dicts()
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
    by_row = {record.raw_row_number: record.id for record in persisted}
    result: list[AgentInputMark] = []
    for mark in marks:
        row_number = mark.safe_evidence.get("row_number")
        if not isinstance(row_number, int) or row_number not in by_row:
            raise ValueError("ingestion mark does not correspond to a persisted input")
        result.append(mark.model_copy(update={"input_record_id": by_row[row_number]}))
    return tuple(result)


def _safe_csv_problem_code(error: CsvFormatError | AgentContractError) -> str:
    if isinstance(error, CsvFormatError):
        return "csv_format_invalid"
    return "fixed_field_mapping_unresolved"


def _csv_source_profiles(
    materials: dict[str, tuple[Snapshot, SourceFile]],
) -> tuple[
    tuple[CsvSourceSchemaProfile, CsvSourceSchemaProfile],
    dict[str, dict[str, str]],
]:
    profiles: list[CsvSourceSchemaProfile] = []
    field_refs: dict[str, dict[str, str]] = {}
    mapper = AgentContractMapper()
    for role in ("authoritative", "target"):
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
    return (profiles[0], profiles[1]), field_refs


def _infer_csv_column_type(values: list[str]) -> str:
    if not values:
        return "unknown"
    sample = values[:50]
    if sum("@" in value for value in sample) * 2 >= len(sample):
        return "email"
    compact_digits = [
        "".join(character for character in value if character.isdigit()) for value in sample
    ]
    if sum(len(value) == 11 and value.startswith("1") for value in compact_digits) * 2 >= len(
        sample
    ):
        return "phone"
    if len(set(sample)) == len(sample):
        return "identifier"
    return "text"


def _deterministic_csv_pair_mapping(
    profiles: tuple[CsvSourceSchemaProfile, CsvSourceSchemaProfile],
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
        refs_by_header = {column.header: column.source_field_ref for column in profile.columns}
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
) -> CsvSchemaMappingOutput:
    if not isinstance(output, CsvSchemaMappingOutput):
        raise ValueError("CSV mapping Skill returned another schema")
    for role, mappings in (
        ("authoritative", output.authoritative_mappings),
        ("target", output.target_mappings),
    ):
        contract_fields = [mapping.contract_field for mapping in mappings]
        source_refs = [mapping.source_field_ref for mapping in mappings]
        if len(set(contract_fields)) != len(contract_fields):
            raise ValueError(f"{role} CSV mapping repeats a contract field")
        if len(set(source_refs)) != len(source_refs):
            raise ValueError(f"{role} CSV mapping repeats a source field")
        for mapping in mappings:
            if mapping.source_field_ref not in field_refs[role]:
                raise ValueError(f"{role} CSV mapping references an unknown source field")
            if mapping.normalizer_id != _normalizer_for_contract_field(mapping.contract_field):
                raise ValueError(f"{role} CSV mapping uses an invalid normalizer")
            if mapping.contract_field == "class_name" and mapping.entity_kinds != ("student",):
                raise ValueError("class_name CSV mapping only applies to students")
    allowed_unresolved = {
        f"{role}.{field}"
        for role in ("authoritative", "target")
        for field in ("category", "name", "number", "class_name", "phone", "email")
    }
    if not set(output.unresolved_required_fields) <= allowed_unresolved:
        raise ValueError("CSV mapping returned an unknown unresolved field")
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
            item.contract_field: field_refs[role][item.source_field_ref] for item in items
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
) -> DatabaseSchemaMappingOutput:
    by_role: dict[str, tuple[DatabaseFieldMapping, ...]] = {}
    for role in ("authoritative", "target"):
        refs_by_column = {column: field_ref for field_ref, column in field_refs[role].items()}
        by_role[role] = tuple(
            DatabaseFieldMapping(
                source_field_ref=refs_by_column[column],
                contract_field=field,
                entity_kinds=(
                    ("student",) if field == "class_name" else ("department", "student", "teacher")
                ),
                normalizer_id=_normalizer_for_contract_field(field),
            )
            for field, column in configured_mappings[role].items()
        )
    return DatabaseSchemaMappingOutput(
        schema_version="fixed-six-field-sql-mapping-v2",
        authoritative_mappings=by_role["authoritative"],
        target_mappings=by_role["target"],
        unresolved_required_fields=(),
    )


def _validate_database_mapping_output(
    output: object,
    *,
    field_refs: dict[str, dict[str, str]],
    configured_target_mapping: dict[str, str],
) -> DatabaseSchemaMappingOutput:
    if not isinstance(output, DatabaseSchemaMappingOutput):
        raise ValueError("database mapping Skill returned another schema")
    unresolved = set(output.unresolved_required_fields)
    allowed_unresolved = {
        f"{role}.{field}"
        for role in ("authoritative", "target")
        for field in _fixed_contract_fields()
    }
    if not unresolved <= allowed_unresolved:
        raise ValueError("database mapping returned an unknown unresolved field")

    compiled: dict[str, dict[str, str]] = {}
    for role, mappings in (
        ("authoritative", output.authoritative_mappings),
        ("target", output.target_mappings),
    ):
        contract_fields = [mapping.contract_field for mapping in mappings]
        source_refs = [mapping.source_field_ref for mapping in mappings]
        if len(set(contract_fields)) != len(contract_fields):
            raise ValueError(f"{role} database mapping repeats a contract field")
        if len(set(source_refs)) != len(source_refs):
            raise ValueError(f"{role} database mapping repeats a source field")
        compiled[role] = {}
        for mapping in mappings:
            if mapping.source_field_ref not in field_refs[role]:
                raise ValueError(f"{role} database mapping references an unknown source field")
            if mapping.normalizer_id != _normalizer_for_contract_field(mapping.contract_field):
                raise ValueError(f"{role} database mapping uses an invalid normalizer")
            if mapping.contract_field == "class_name" and mapping.entity_kinds != ("student",):
                raise ValueError("class_name database mapping only applies to students")
            compiled[role][mapping.contract_field] = field_refs[role][mapping.source_field_ref]
        missing = _fixed_contract_fields().difference(compiled[role])
        unreported = {f"{role}.{field}" for field in missing}.difference(unresolved)
        if unreported:
            raise ValueError(f"{role} database mapping omitted fields without marking unresolved")
    if compiled["target"] != configured_target_mapping:
        raise ValueError("target database mapping differs from the server write allow-list")
    return output


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
            item.contract_field: field_refs[role][item.source_field_ref] for item in items
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


def _outcome(action: AllowedActionV1) -> GraphActionOutcome:
    return GraphActionOutcome(
        action_id=action.action_id,
        evidence_refs=action.required_evidence,
    )


def _task_uses_database(task: ReconciliationTask | None) -> bool:
    if task is None or not isinstance(task.agent_intent, dict):
        return False
    for key in ("source", "target"):
        selection = task.agent_intent.get(key)
        if isinstance(selection, dict) and selection.get("kind") == "database":
            return True
    return task.agent_intent.get("source_mode") == "database"


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

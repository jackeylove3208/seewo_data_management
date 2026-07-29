"""Production candidate planning for the controlled Agent graph runtime.

The durable provider narrows these templates with server-owned facts.  Keeping the
complete vocabulary here makes it impossible for the launcher to invent an action
that is absent from the reviewed graph definition.
"""

import hashlib
from pathlib import Path
from typing import Literal
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_graph.contracts import (
    AllowedActionV1,
    CandidateActionEvaluationV1,
    SingleActionReasonCode,
)
from app.agent_graph.worker import GraphCandidatePlan, GraphWorkContext
from app.agent_runtime.source_bindings import resolve_source_bindings
from app.agent_runtime.state_machine import AgentPhase
from app.connectors.database_runtime import DatabaseConnectorResolver
from app.ingestion.csv_reader import inspect_csv, read_csv_frame
from app.models.agent_analysis import (
    AgentClarificationRecord,
    AgentGovernanceOperationRecord,
    AgentGovernancePlanRecord,
    AgentInputRecord,
    AgentModelBatchItemRecord,
    AgentModelBatchRecord,
)
from app.models.api_connectors import ApiAuthoritySourceRecord
from app.models.reconciliation import ReconciliationTask
from app.models.remote_sources import RemoteSourceRecord
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.executions import ExecutionRepository

ActionKind = Literal[
    "dispatch_sub_agent",
    "run_deterministic",
    "wait_human",
    "terminate",
]
RiskKind = Literal["low", "medium", "high"]
MAX_EXECUTION_BATCH_SIZE = 50


def _action(
    action_id: str,
    *,
    successor: str,
    kind: ActionKind = "run_deterministic",
    sub_agent: str | None = None,
    risk: RiskKind = "low",
    requires_human: bool = False,
    graph_action_kind: str | None = None,
    resource_ids: tuple[str, ...] | None = None,
    required_evidence: tuple[str, ...] | None = None,
) -> AllowedActionV1:
    return AllowedActionV1(
        action_id=action_id,
        graph_action_kind=graph_action_kind or action_id,
        kind=kind,
        sub_agent=sub_agent,
        resource_ids=resource_ids or (f"runtime:{action_id}",),
        required_evidence=required_evidence or (f"result:{action_id}",),
        risk=risk,
        requires_human=requires_human,
        successor_node=successor,
    )


def _execution_batch_action(
    context: GraphWorkContext,
    *,
    plan_id: UUID,
    operation_ids: tuple[UUID, ...],
) -> AllowedActionV1:
    bounded_ids = operation_ids[:MAX_EXECUTION_BATCH_SIZE]
    if not bounded_ids:
        raise ValueError("execution batch requires at least one ready operation")
    return _action(
        "execute_operations_batch",
        graph_action_kind="verify_operations",
        successor="verify_operations",
        kind=(
            "run_deterministic"
            if context.execution_contract_version == "deterministic-execution-v2"
            else "dispatch_sub_agent"
        ),
        sub_agent=(
            None
            if context.execution_contract_version == "deterministic-execution-v2"
            else "governance-execution"
        ),
        risk="high",
        resource_ids=(
            f"execution-plan:{plan_id}",
            *(f"operation:{item}" for item in bounded_ids),
        ),
        required_evidence=tuple(f"execution-outcome:{item}" for item in bounded_ids),
    )


_SYNC_TEMPLATES: dict[str, tuple[AllowedActionV1, ...]] = {
    "intent_confirmed": (_action("acquire_school_lock", successor="acquire_school_lock"),),
    "acquire_school_lock": (_action("inspect_sources", successor="inspect_sources"),),
    "inspect_sources": (
        _action(
            "inspect_authority",
            successor="inspect_sources",
            kind="dispatch_sub_agent",
            sub_agent="source-inspection",
        ),
        _action(
            "inspect_target",
            successor="inspect_sources",
            kind="dispatch_sub_agent",
            sub_agent="source-inspection",
        ),
        _action("normalize_ready_sources", successor="normalize_input_batches"),
    ),
    "normalize_input_batches": (
        _action(
            "normalize_next_batch",
            successor="normalize_input_batches",
            kind="dispatch_sub_agent",
            sub_agent="input-normalization",
        ),
        _action("validate_normalized_input", successor="validate_input_contract"),
    ),
    "validate_input_contract": (
        _action("report_abnormal_input", successor="abnormal_input_report"),
        _action("build_identity_index", successor="build_identity_index"),
    ),
    "abnormal_input_report": (
        _action(
            "finish_abnormal_report",
            successor="terminal",
            kind="dispatch_sub_agent",
            sub_agent="reporting",
        ),
    ),
    "build_identity_index": (_action("build_identity_index", successor="construct_identity_work"),),
    "construct_identity_work": (
        _action("construct_identity_work", successor="analyze_actionable_batches"),
    ),
    "analyze_actionable_batches": (
        _action(
            "analyze_next_batch",
            successor="analyze_actionable_batches",
            kind="dispatch_sub_agent",
            sub_agent="reconciliation-analysis",
        ),
        _action(
            "repair_analysis_batch",
            successor="repair_analysis_batch",
            kind="dispatch_sub_agent",
            sub_agent="reconciliation-analysis",
        ),
        _action(
            "resolve_identity_conflicts",
            successor="resolve_identity_conflicts",
            kind="wait_human",
            risk="high",
            requires_human=True,
        ),
        _action("enter_aggregate_risk", successor="aggregate_risk"),
    ),
    "repair_analysis_batch": (
        _action(
            "repair_analysis_batch",
            successor="analyze_actionable_batches",
            kind="dispatch_sub_agent",
            sub_agent="reconciliation-analysis",
        ),
    ),
    "resolve_identity_conflicts": (
        _action(
            "resume_analysis_after_identity_conflicts",
            successor="analyze_actionable_batches",
        ),
    ),
    "aggregate_risk": (_action("aggregate_risk", successor="wait_high_risk_approvals"),),
    "wait_high_risk_approvals": (
        _action("compile_execution_plan", successor="compile_execution_plan"),
    ),
    "compile_execution_plan": (_action("preflight_execution", successor="preflight_execution"),),
    "preflight_execution": (
        _action(
            "request_cross_phase_replan",
            successor="wait_replan_confirmation",
            kind="wait_human",
            risk="high",
            requires_human=True,
        ),
        _action("execute_ready_operations", successor="execute_ready_operations"),
    ),
    "wait_replan_confirmation": (
        _action("compile_execution_plan", successor="compile_execution_plan"),
    ),
    "execute_ready_operations": (
        _action(
            "verify_operations",
            successor="verify_operations",
            kind="dispatch_sub_agent",
            sub_agent="governance-execution",
            risk="high",
        ),
    ),
    "verify_operations": (
        _action(
            "execute_remaining_independent",
            successor="execute_remaining_independent",
            kind="dispatch_sub_agent",
            sub_agent="governance-execution",
            risk="high",
        ),
        _action("generate_terminal_report", successor="generate_terminal_report"),
    ),
    "execute_remaining_independent": (
        _action(
            "verify_operations",
            successor="verify_operations",
            kind="dispatch_sub_agent",
            sub_agent="governance-execution",
            risk="high",
        ),
    ),
    "generate_terminal_report": (
        _action(
            "finish_terminal_report",
            successor="terminal",
            kind="dispatch_sub_agent",
            sub_agent="reporting",
        ),
    ),
    "drain_current_atomic_unit": (_action("termination_report", successor="termination_report"),),
    "termination_report": (
        _action(
            "finish_termination_report",
            successor="terminal",
            kind="dispatch_sub_agent",
            sub_agent="reporting",
        ),
    ),
    "blocked_model_error": (
        _action(
            "terminate_blocked_run",
            successor="drain_current_atomic_unit",
            kind="terminate",
        ),
    ),
}


_SYNC_TEMPLATES_V2: dict[str, tuple[AllowedActionV1, ...]] = {
    **_SYNC_TEMPLATES,
    "acquire_school_lock": (
        _action("materialize_sources", successor="materialize_sources"),
    ),
    "materialize_sources": (
        _action(
            "materialize_remote_authority",
            successor="inspect_sources",
            resource_ids=("remote-source:current",),
            required_evidence=("remote-source:materialized",),
        ),
    ),
}


_ROLLBACK_TEMPLATES: dict[str, tuple[AllowedActionV1, ...]] = {
    "rollback_intent_confirmed": (_action("acquire_school_lock", successor="acquire_school_lock"),),
    "acquire_school_lock": (
        _action("load_verified_mutations", successor="load_verified_mutations"),
    ),
    "load_verified_mutations": (
        _action(
            "assess_restore_impact",
            successor="assess_restore_impact",
            kind="dispatch_sub_agent",
            sub_agent="rollback-analysis",
        ),
    ),
    "assess_restore_impact": (
        _action(
            "assess_restore_impact",
            successor="wait_restore_conflicts",
            kind="dispatch_sub_agent",
            sub_agent="rollback-analysis",
            risk="high",
        ),
    ),
    "wait_restore_conflicts": (
        _action(
            "wait_rollback_approval",
            successor="wait_rollback_approval",
            kind="wait_human",
            risk="high",
            requires_human=True,
        ),
    ),
    "wait_rollback_approval": (_action("compile_restore_plan", successor="compile_restore_plan"),),
    "compile_restore_plan": (_action("preflight_restore", successor="preflight_restore"),),
    "preflight_restore": (
        _action("execute_restore_operations", successor="execute_restore_operations"),
    ),
    "execute_restore_operations": (
        _action(
            "verify_restore_operations",
            successor="verify_restore_operations",
            kind="dispatch_sub_agent",
            sub_agent="rollback-execution",
            risk="high",
        ),
    ),
    "verify_restore_operations": (
        _action("generate_rollback_report", successor="generate_rollback_report"),
    ),
    "generate_rollback_report": (
        _action(
            "finish_rollback_report",
            successor="terminal",
            kind="dispatch_sub_agent",
            sub_agent="reporting",
        ),
    ),
    "drain_current_atomic_unit": (_action("termination_report", successor="termination_report"),),
    "termination_report": (
        _action(
            "finish_termination_report",
            successor="terminal",
            kind="dispatch_sub_agent",
            sub_agent="reporting",
        ),
    ),
    "blocked_model_error": (
        _action(
            "terminate_blocked_run",
            successor="drain_current_atomic_unit",
            kind="terminate",
        ),
    ),
}


def production_candidate_templates(
    node: str,
    *,
    graph_version: str = "agent-sync-graph-v1",
) -> tuple[AllowedActionV1, ...]:
    if graph_version == "agent-sync-graph-v1":
        templates = _SYNC_TEMPLATES
    elif graph_version == "agent-sync-graph-v2":
        templates = _SYNC_TEMPLATES_V2
    elif graph_version == "agent-rollback-graph-v1":
        templates = _ROLLBACK_TEMPLATES
    else:
        raise ValueError(f"unsupported Agent graph version: {graph_version}")
    return templates.get(node, ())


class ProductionGraphCandidateProvider:
    """Project all currently safe actions from durable, server-owned facts."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        database_connectors: DatabaseConnectorResolver | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._database_connectors = database_connectors

    async def __call__(self, context: GraphWorkContext) -> GraphCandidatePlan:
        async with self._session_factory() as session:
            actions = await self._actions(session, context)
        if not actions:
            raise RuntimeError(
                f"no production graph action is available for {context.current_node}"
            )
        passed_kinds = {action.graph_action_kind or action.action_id for action in actions}
        evaluations = [
            CandidateActionEvaluationV1(action=action, passed=True) for action in actions
        ]
        for template in production_candidate_templates(
            context.current_node,
            graph_version=context.graph_version,
        ):
            action_kind = template.graph_action_kind or template.action_id
            if action_kind not in passed_kinds:
                evaluations.append(
                    CandidateActionEvaluationV1(
                        action=template,
                        passed=False,
                        rejected_guard_codes=(
                            _rejected_guard_code(
                                context.current_node,
                                action_kind,
                                passed_kinds,
                            ),
                        ),
                    )
                )
        singleton = _single_action_reason(context, actions)
        return GraphCandidatePlan(
            candidate_evaluations=tuple(evaluations),
            single_action_reason_code=singleton,
        )

    async def _actions(
        self,
        session: AsyncSession,
        context: GraphWorkContext,
    ) -> tuple[AllowedActionV1, ...]:
        from app.models.agent_graph import AgentGraphRunRecord

        termination_requested = await session.scalar(
            select(AgentGraphRunRecord.termination_requested).where(
                AgentGraphRunRecord.id == context.graph_run_id
            )
        )
        if termination_requested and context.current_node not in {
            "drain_current_atomic_unit",
            "termination_report",
            "terminal",
        }:
            return (
                _action(
                    "terminate_requested",
                    graph_action_kind="terminate_requested",
                    successor="drain_current_atomic_unit",
                    kind="terminate",
                    required_evidence=("termination-request:accepted",),
                ),
            )
        if context.current_node == "inspect_sources":
            return await self._inspection_actions(session, context)
        if context.current_node == "materialize_sources":
            if context.ingestion_contract_version == "source-ingestion-v3":
                api_source_id = await session.scalar(
                    select(ApiAuthoritySourceRecord.id).where(
                        ApiAuthoritySourceRecord.task_id == context.task_id,
                        ApiAuthoritySourceRecord.tenant_id == context.tenant_id,
                        ApiAuthoritySourceRecord.state.in_(
                            ("registered", "materializing", "ready", "failed")
                        ),
                    )
                )
                if api_source_id is None:
                    raise LookupError("Task-bound API authority source is missing")
                template = _template(context, "materialize_remote_authority")
                return (
                    template.model_copy(
                        update={
                            "resource_ids": (f"api-source:{api_source_id}",),
                            "required_evidence": (
                                f"api-source:{api_source_id}:materialized",
                            ),
                        }
                    ),
                )
            remote_source_id = await session.scalar(
                select(RemoteSourceRecord.id).where(
                    RemoteSourceRecord.task_id == context.task_id,
                    RemoteSourceRecord.tenant_id == context.tenant_id,
                    RemoteSourceRecord.state.in_(
                        ("registered", "materializing", "ready", "failed")
                    ),
                )
            )
            if remote_source_id is None:
                raise LookupError("Task-bound remote source is missing")
            template = _template(context, "materialize_remote_authority")
            return (
                template.model_copy(
                    update={
                        "resource_ids": (f"remote-source:{remote_source_id}",),
                        "required_evidence": (
                            f"remote-source:{remote_source_id}:materialized",
                        ),
                    }
                ),
            )
        if context.current_node == "normalize_input_batches":
            return await self._normalization_actions(session, context)
        if context.current_node == "validate_input_contract":
            return await self._validation_action(session, context)
        if context.current_node == "analyze_actionable_batches":
            return await self._analysis_actions(session, context)
        if context.current_node == "repair_analysis_batch":
            return await self._analysis_actions(session, context, repair=True)
        if context.current_node == "aggregate_risk":
            return (_template(context, "aggregate_risk"),)
        if context.current_node == "verify_operations":
            pending_count = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(AgentGovernanceOperationRecord)
                        .where(
                            AgentGovernanceOperationRecord.run_id == context.run_id,
                            AgentGovernanceOperationRecord.status == "pending",
                        )
                    )
                )
                or 0
            )
            action_id = (
                "execute_remaining_independent" if pending_count else "generate_terminal_report"
            )
            return (_template(context, action_id),)
        if context.current_node in {
            "execute_ready_operations",
            "execute_remaining_independent",
        }:
            plan = await session.scalar(
                select(AgentGovernancePlanRecord)
                .where(AgentGovernancePlanRecord.run_id == context.run_id)
                .order_by(AgentGovernancePlanRecord.created_at.desc())
            )
            if plan is None:
                return (_template(context, "verify_operations"),)
            operations = tuple(
                await session.scalars(
                    select(AgentGovernanceOperationRecord)
                    .where(AgentGovernanceOperationRecord.plan_id == plan.id)
                    .order_by(AgentGovernanceOperationRecord.id)
                )
            )
            status_by_id = {item.id: item.status for item in operations}
            ready = tuple(
                item
                for item in operations
                if item.status == "pending"
                and all(
                    status_by_id.get(UUID(str(dependency))) == "succeeded"
                    for dependency in item.dependencies
                )
            )
            if not ready:
                return (_template(context, "verify_operations"),)
            return (
                _execution_batch_action(
                    context,
                    plan_id=plan.id,
                    operation_ids=tuple(item.id for item in ready),
                ),
            )
        if context.current_node in {
            "generate_terminal_report",
            "abnormal_input_report",
            "termination_report",
            "generate_rollback_report",
        }:
            templates = production_candidate_templates(
                context.current_node,
                graph_version=context.graph_version,
            )
            action = templates[0]
            fact_ref = f"report-facts:{context.run_id}:{context.graph_cursor}"
            return (
                action.model_copy(
                    update={
                        "resource_ids": (fact_ref,),
                        "required_evidence": (fact_ref,),
                    }
                ),
            )
        templates = production_candidate_templates(
            context.current_node,
            graph_version=context.graph_version,
        )
        if len(templates) == 1:
            return templates
        if context.current_node == "preflight_execution":
            plan = await session.scalar(
                select(AgentGovernancePlanRecord)
                .where(AgentGovernancePlanRecord.run_id == context.run_id)
                .order_by(AgentGovernancePlanRecord.created_at.desc())
            )
            current = await ExecutionRepository(session).current_target_version(context.task_id)
            task = await session.get(ReconciliationTask, context.task_id)
            external_version_hash: str | None = None
            connector_id = _database_target_connector_id(task)
            if connector_id is not None:
                if self._database_connectors is None:
                    raise RuntimeError("SQL connector runtime is unavailable")
                connector = await self._database_connectors.connector(connector_id)
                external_version_hash = hashlib.sha256(
                    (await connector.version()).value.encode()
                ).hexdigest()
            stale = plan is not None and (
                current is None
                or f"sha256:{current.file_sha256}" != plan.target_version
                or (
                    external_version_hash is not None
                    and current.file_sha256 != external_version_hash
                )
            )
            return (
                _template(
                    context,
                    "request_cross_phase_replan" if stale else "execute_ready_operations",
                ),
            )
        if context.current_node == "assess_restore_impact":
            return (_template(context, "assess_restore_impact"),)
        if (
            context.current_node == "execute_restore_operations"
            and context.execution_contract_version == "deterministic-execution-v2"
        ):
            action = _template(context, "verify_restore_operations")
            return (
                action.model_copy(
                    update={
                        "kind": "run_deterministic",
                        "sub_agent": None,
                    }
                ),
            )
        return templates

    async def _inspection_actions(
        self,
        session: AsyncSession,
        context: GraphWorkContext,
    ) -> tuple[AllowedActionV1, ...]:
        completed = set(await session.scalars(select_transition_action_ids(context.graph_run_id)))
        actions: list[AllowedActionV1] = []
        for role, action_kind in (
            ("authoritative", "inspect_authority"),
            ("target", "inspect_target"),
        ):
            action_id = f"{action_kind}:source"
            if action_id not in completed:
                if context.ingestion_contract_version in {
                    "source-ingestion-v2",
                    "source-ingestion-v3",
                }:
                    return (
                        _action(
                            action_id,
                            graph_action_kind=action_kind,
                            successor="inspect_sources",
                            kind="run_deterministic",
                            resource_ids=(f"source:{role}:full",),
                            required_evidence=(f"source:{role}:inspection",),
                        ),
                    )
                actions.append(
                    _action(
                        action_id,
                        graph_action_kind=action_kind,
                        successor="inspect_sources",
                        kind="dispatch_sub_agent",
                        sub_agent="source-inspection",
                        resource_ids=(f"source:{role}:page:1",),
                        required_evidence=(f"source:{role}:inspection",),
                    )
                )
        if actions:
            return (actions[0],)
        return (
            _action(
                "normalize_ready_sources:pair",
                graph_action_kind="normalize_ready_sources",
                successor="normalize_input_batches",
                resource_ids=("snapshot-pair:current",),
                required_evidence=("normalization-work:ready",),
            ),
        )

    async def _normalization_actions(
        self,
        session: AsyncSession,
        context: GraphWorkContext,
    ) -> tuple[AllowedActionV1, ...]:
        completed = set(await session.scalars(select_transition_action_ids(context.graph_run_id)))
        if context.ingestion_contract_version == "source-ingestion-v3":
            from app.agent_runtime.repository import AgentRuntimeRepository

            task = await session.get(ReconciliationTask, context.task_id)
            if task is None:
                raise LookupError("Agent graph task is missing")
            bindings = resolve_source_bindings(task)
            runtime = AgentRuntimeRepository(session)
            inspections = tuple(
                [
                    await runtime.get_checkpoint(
                        context.run_id,
                        phase=AgentPhase.INGEST_AND_NORMALIZE,
                        checkpoint_key=f"graph-source-inspection:{binding.role}",
                    )
                    for binding in bindings
                ]
            )
            if any(
                checkpoint is None
                or not checkpoint.payload.get("recognized", False)
                for checkpoint in inspections
            ):
                return (
                    _action(
                        "validate_normalized_input",
                        successor="validate_input_contract",
                    ),
                )
            for binding in bindings:
                mapping = await runtime.get_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=binding.mapping_checkpoint_key,
                )
                if mapping is None:
                    return (
                        _action(
                            f"resolve_{binding.connector_kind}_{binding.role}_mapping",
                            graph_action_kind="normalize_next_batch",
                            successor="normalize_input_batches",
                            kind="run_deterministic",
                            resource_ids=(f"source:{binding.role}:mapping",),
                            required_evidence=(
                                f"mapping:{binding.connector_kind}:"
                                f"{binding.role}:v3",
                            ),
                        ),
                    )
                if not mapping.payload.get("resolved", False):
                    return (
                        _action(
                            "validate_normalized_input",
                            successor="validate_input_contract",
                        ),
                    )
            for binding in bindings:
                normalization = await runtime.get_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=binding.normalization_checkpoint_key,
                )
                if normalization is None:
                    return (
                        _action(
                            f"normalize_{binding.role}_full",
                            graph_action_kind="normalize_next_batch",
                            successor="normalize_input_batches",
                            kind="run_deterministic",
                            resource_ids=(f"source:{binding.role}:full",),
                            required_evidence=(f"normalized:{binding.role}:full",),
                        ),
                    )
            return (
                _action(
                    "validate_normalized_input",
                    successor="validate_input_contract",
                ),
            )
        if context.ingestion_contract_version == "source-ingestion-v2":
            from app.agent_runtime.repository import AgentRuntimeRepository

            runtime = AgentRuntimeRepository(session)
            source_mode = await _source_pair_mode(session, context.task_id)
            mapping_checkpoint_key = (
                "graph-database-field-mapping-v2"
                if source_mode == "database"
                else "graph-csv-field-mapping-v2"
            )
            mapping = await runtime.get_checkpoint(
                context.run_id,
                phase=AgentPhase.INGEST_AND_NORMALIZE,
                checkpoint_key=mapping_checkpoint_key,
            )
            if mapping is None:
                inspections = tuple(
                    [
                        await runtime.get_checkpoint(
                            context.run_id,
                            phase=AgentPhase.INGEST_AND_NORMALIZE,
                            checkpoint_key=f"graph-source-inspection:{role}",
                        )
                        for role in ("authoritative", "target")
                    ]
                )
                fatal_inspection = any(
                    checkpoint is None
                    or (
                        not checkpoint.payload.get("recognized", False)
                        and not checkpoint.payload.get("mapping_required", False)
                    )
                    for checkpoint in inspections
                )
                if fatal_inspection:
                    return (
                        _action(
                            "validate_normalized_input",
                            successor="validate_input_contract",
                        ),
                    )
                mapping_required = any(
                    checkpoint.payload.get("mapping_required", False)
                    or (
                        source_mode in {"csv", "remote_csv"}
                        and not checkpoint.payload.get("recognized", False)
                    )
                    for checkpoint in inspections
                    if checkpoint is not None
                )
                remote_csv = source_mode == "remote_csv"
                return (
                    _action(
                        (
                            "resolve_database_fixed_field_mapping"
                            if source_mode == "database"
                            else "resolve_csv_fixed_field_mapping"
                        ),
                        graph_action_kind="normalize_next_batch",
                        successor="normalize_input_batches",
                        kind=("dispatch_sub_agent" if mapping_required else "run_deterministic"),
                        sub_agent=(
                            (
                                "database-schema-mapping"
                                if source_mode == "database"
                                else (
                                    "remote-csv-schema-mapping"
                                    if remote_csv
                                    else "csv-schema-mapping"
                                )
                            )
                            if mapping_required
                            else None
                        ),
                        resource_ids=(
                            (
                                "source-pair:current",
                                "source:authoritative:page:1",
                                "source:target:page:1",
                            )
                            if remote_csv
                            else ("source-pair:current",)
                        ),
                        required_evidence=("mapping:fixed-six-field-v2",),
                    ),
                )
            if not mapping.payload.get("resolved", False):
                return (
                    _action(
                        "validate_normalized_input",
                        successor="validate_input_contract",
                    ),
                )
            for role in ("authoritative", "target"):
                action_id = f"normalize_{role}_full"
                if action_id not in completed:
                    return (
                        _action(
                            action_id,
                            graph_action_kind="normalize_next_batch",
                            successor="normalize_input_batches",
                            kind="run_deterministic",
                            resource_ids=(f"source:{role}:full",),
                            required_evidence=(f"normalized:{role}:full",),
                        ),
                    )
            return (
                _action(
                    "validate_normalized_input",
                    successor="validate_input_contract",
                ),
            )
        actions: list[AllowedActionV1] = []
        for role, page_count in await _source_page_counts(
            session,
            task_id=context.task_id,
        ):
            next_page = next(
                (
                    page
                    for page in range(1, page_count + 1)
                    if f"normalize_{role}_page_{page}" not in completed
                ),
                None,
            )
            if next_page is not None:
                resource_id = f"source:{role}:page:{next_page}"
                actions.append(
                    _action(
                        f"normalize_{role}_page_{next_page}",
                        graph_action_kind="normalize_next_batch",
                        successor="normalize_input_batches",
                        kind="dispatch_sub_agent",
                        sub_agent="input-normalization",
                        resource_ids=(resource_id,),
                        required_evidence=(f"normalized:{role}:page:{next_page}",),
                    )
                )
        if actions:
            return (actions[0],)
        return (
            _action(
                "validate_normalized_input",
                successor="validate_input_contract",
            ),
        )

    async def _validation_action(
        self,
        session: AsyncSession,
        context: GraphWorkContext,
    ) -> tuple[AllowedActionV1, ...]:
        from app.agent_runtime.repository import AgentRuntimeRepository

        runtime = AgentRuntimeRepository(session)
        if context.ingestion_contract_version == "source-ingestion-v3":
            task = await session.get(ReconciliationTask, context.task_id)
            if task is None:
                raise LookupError("Agent graph task is missing")
            bindings = resolve_source_bindings(task)
            inspections = tuple(
                [
                    await runtime.get_checkpoint(
                        context.run_id,
                        phase=AgentPhase.INGEST_AND_NORMALIZE,
                        checkpoint_key=f"graph-source-inspection:{binding.role}",
                    )
                    for binding in bindings
                ]
            )
            mappings = tuple(
                [
                    await runtime.get_checkpoint(
                        context.run_id,
                        phase=AgentPhase.INGEST_AND_NORMALIZE,
                        checkpoint_key=binding.mapping_checkpoint_key,
                    )
                    for binding in bindings
                ]
            )
            normalizations = tuple(
                [
                    await runtime.get_checkpoint(
                        context.run_id,
                        phase=AgentPhase.INGEST_AND_NORMALIZE,
                        checkpoint_key=binding.normalization_checkpoint_key,
                    )
                    for binding in bindings
                ]
            )
            input_counts = {
                binding.role: int(
                    (
                        await session.scalar(
                            select(func.count())
                            .select_from(AgentInputRecord)
                            .where(
                                AgentInputRecord.run_id == context.run_id,
                                AgentInputRecord.task_id == context.task_id,
                                AgentInputRecord.tenant_id == context.tenant_id,
                                AgentInputRecord.source_role == binding.role,
                            )
                        )
                    )
                    or 0
                )
                for binding in bindings
            }
            snapshot_roles = tuple(
                await session.scalars(
                    select(Snapshot.source_role)
                    .where(Snapshot.task_id == context.task_id)
                    .order_by(Snapshot.source_role)
                )
            )
            abnormal = (
                any(
                    checkpoint is None
                    or not checkpoint.payload.get("recognized", False)
                    for checkpoint in inspections
                )
                or any(
                    checkpoint is None
                    or not checkpoint.payload.get("resolved", False)
                    for checkpoint in mappings
                )
                or any(
                    checkpoint is None
                    or checkpoint.payload.get("record_count")
                    != input_counts[binding.role]
                    for binding, checkpoint in zip(
                        bindings,
                        normalizations,
                        strict=True,
                    )
                )
                or snapshot_roles != ("authoritative", "target")
                or input_counts["authoritative"] <= 0
            )
            return (
                _template(
                    context,
                    "report_abnormal_input" if abnormal else "build_identity_index",
                ),
            )
        if context.ingestion_contract_version == "source-ingestion-v2":
            source_mode = await _source_pair_mode(session, context.task_id)
            mapping = await runtime.get_checkpoint(
                context.run_id,
                phase=AgentPhase.INGEST_AND_NORMALIZE,
                checkpoint_key=(
                    "graph-database-field-mapping-v2"
                    if source_mode == "database"
                    else "graph-csv-field-mapping-v2"
                ),
            )
            normalizations = tuple(
                [
                    await runtime.get_checkpoint(
                        context.run_id,
                        phase=AgentPhase.INGEST_AND_NORMALIZE,
                        checkpoint_key=f"graph-source-normalization:{role}",
                    )
                    for role in ("authoritative", "target")
                ]
            )
            abnormal = (
                mapping is None
                or not mapping.payload.get("resolved", False)
                or any(checkpoint is None for checkpoint in normalizations)
            )
            return (
                _template(
                    context,
                    "report_abnormal_input" if abnormal else "build_identity_index",
                ),
            )
        legacy_inspections = []
        for role in ("authoritative", "target"):
            legacy_inspections.append(
                await runtime.get_checkpoint(
                    context.run_id,
                    phase=AgentPhase.INGEST_AND_NORMALIZE,
                    checkpoint_key=f"graph-source-inspection:{role}",
                )
            )
        abnormal = any(
            checkpoint is None or not checkpoint.payload.get("recognized", False)
            for checkpoint in legacy_inspections
        )
        return (
            _template(
                context,
                "report_abnormal_input" if abnormal else "build_identity_index",
            ),
        )

    async def _analysis_actions(
        self,
        session: AsyncSession,
        context: GraphWorkContext,
        *,
        repair: bool = False,
    ) -> tuple[AllowedActionV1, ...]:
        if not repair:
            unresolved = await session.scalar(
                select(AgentClarificationRecord.id).where(
                    AgentClarificationRecord.run_id == context.run_id,
                    AgentClarificationRecord.status.in_(("pending", "interpreted")),
                )
            )
            if unresolved is not None:
                return (_template(context, "resolve_identity_conflicts"),)
        batches = tuple(
            await session.scalars(
                select(AgentModelBatchRecord)
                .where(
                    AgentModelBatchRecord.run_id == context.run_id,
                    AgentModelBatchRecord.status == "pending",
                )
                .order_by(
                    AgentModelBatchRecord.entity_kind,
                    AgentModelBatchRecord.created_at,
                    AgentModelBatchRecord.id,
                )
            )
        )
        actions: list[AllowedActionV1] = []
        for batch in batches:
            work_ids = tuple(
                await session.scalars(
                    select(AgentModelBatchItemRecord.work_item_id)
                    .where(AgentModelBatchItemRecord.batch_id == batch.id)
                    .order_by(AgentModelBatchItemRecord.ordinal)
                )
            )
            resources = tuple(f"work-item:{item}" for item in work_ids)
            evidence = tuple(f"paired-record:{item}" for item in work_ids)
            actions.append(
                _action(
                    (
                        f"repair_batch_{str(batch.id)[:8]}"
                        if repair
                        else f"analyze_batch_{str(batch.id)[:8]}"
                    ),
                    graph_action_kind=("repair_analysis_batch" if repair else "analyze_next_batch"),
                    successor="analyze_actionable_batches",
                    kind="dispatch_sub_agent",
                    sub_agent="reconciliation-analysis",
                    resource_ids=resources,
                    required_evidence=evidence,
                )
            )
        if actions:
            return (actions[0],)
        if repair:
            raise RuntimeError("analysis repair node has no pending batch")
        return (_template(context, "enter_aggregate_risk"),)


def _template(context: GraphWorkContext, action_id: str) -> AllowedActionV1:
    for item in production_candidate_templates(
        context.current_node,
        graph_version=context.graph_version,
    ):
        if item.action_id == action_id:
            return item
    raise RuntimeError(f"production action {action_id} is not declared at {context.current_node}")


def _single_action_reason(
    context: GraphWorkContext,
    actions: tuple[AllowedActionV1, ...],
) -> SingleActionReasonCode | None:
    if len(actions) != 1:
        return None
    action = actions[0]
    if action.kind == "terminate":
        return SingleActionReasonCode.TERMINATION_REQUESTED
    if action.kind == "wait_human" or action.requires_human:
        return SingleActionReasonCode.HUMAN_GATE_REQUIRED
    if action.successor_node == "terminal":
        return SingleActionReasonCode.TERMINALIZATION_REQUIRED
    if context.current_node in {
        "intent_confirmed",
        "acquire_school_lock",
        "validate_input_contract",
        "preflight_execution",
        "preflight_restore",
        "drain_current_atomic_unit",
    }:
        return SingleActionReasonCode.SAFETY_MANDATORY
    return SingleActionReasonCode.ONLY_GUARD_SATISFIED


async def _source_pair_mode(session: AsyncSession, task_id: UUID) -> str:
    intent = await session.scalar(
        select(ReconciliationTask.agent_intent).where(ReconciliationTask.id == task_id)
    )
    if not isinstance(intent, dict):
        return "csv"
    source = intent.get("source")
    target = intent.get("target")
    if (
        isinstance(source, dict)
        and isinstance(target, dict)
        and source.get("kind") == "database"
        and target.get("kind") == "database"
    ):
        return "database"
    if (
        isinstance(source, dict)
        and isinstance(target, dict)
        and source.get("kind") == "remote_csv"
        and target.get("kind") == "local"
    ):
        return "remote_csv"
    return "csv"


def _database_target_connector_id(
    task: ReconciliationTask | None,
) -> str | None:
    if task is None or not isinstance(task.agent_intent, dict):
        return None
    target = task.agent_intent.get("target")
    if not isinstance(target, dict) or target.get("kind") != "database":
        return None
    connector_id = target.get("configuration_id")
    return connector_id if isinstance(connector_id, str) and connector_id else None


def select_transition_action_ids(graph_run_id: UUID) -> Select[tuple[str]]:
    from app.models.agent_graph import AgentGraphTransitionRecord

    return select(AgentGraphTransitionRecord.action_id).where(
        AgentGraphTransitionRecord.graph_run_id == graph_run_id
    )


async def _source_page_counts(
    session: AsyncSession,
    *,
    task_id: UUID,
) -> tuple[tuple[str, int], ...]:
    rows = tuple(
        await session.execute(
            select(Snapshot, SourceFile)
            .join(SourceFile, SourceFile.id == Snapshot.source_file_id)
            .where(Snapshot.task_id == task_id)
            .order_by(Snapshot.source_role)
        )
    )
    by_role = {snapshot.source_role: source for snapshot, source in rows}
    result: list[tuple[str, int]] = []
    for role in ("authoritative", "target"):
        source = by_role.get(role)
        if source is None:
            raise LookupError(f"Agent graph source is missing: {role}")
        path = Path(source.storage_path)
        frame = read_csv_frame(path, inspect_csv(path))
        result.append((role, max(1, (frame.height + 49) // 50)))
    return tuple(result)


def _rejected_guard_code(
    node: str,
    action_kind: str,
    passed_kinds: set[str],
) -> str:
    if "terminate_requested" in passed_kinds:
        return "termination_requested"
    if node == "inspect_sources":
        if action_kind == "inspect_target" and "inspect_authority" in passed_kinds:
            return "server_order_deferred"
        return (
            "source_already_inspected"
            if action_kind in {"inspect_authority", "inspect_target"}
            else "source_inspection_incomplete"
        )
    if node == "normalize_input_batches":
        return (
            "no_pending_normalization_batch"
            if action_kind == "normalize_next_batch"
            else "normalization_incomplete"
        )
    if node == "validate_input_contract":
        return (
            "input_contract_valid"
            if action_kind == "report_abnormal_input"
            else "input_contract_invalid"
        )
    if node == "analyze_actionable_batches":
        if (
            "resolve_identity_conflicts" in passed_kinds
            and action_kind != "resolve_identity_conflicts"
        ):
            return "identity_conflict_pending"
        return {
            "analyze_next_batch": "no_pending_analysis_batch",
            "repair_analysis_batch": "no_repairable_analysis_batch",
            "resolve_identity_conflicts": "no_unresolved_identity_conflict",
            "enter_aggregate_risk": "analysis_work_incomplete",
        }.get(action_kind, "server_fact_not_satisfied")
    if node == "preflight_execution":
        return (
            "target_version_current"
            if action_kind == "request_cross_phase_replan"
            else "target_version_stale"
        )
    if node == "verify_operations":
        return (
            "no_pending_operations"
            if action_kind == "execute_remaining_independent"
            else "pending_operations_remain"
        )
    return "server_fact_not_satisfied"

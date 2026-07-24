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
    _rollback_operation,
)
from app.agent_runtime.repository import AgentRuntimeRepository
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
from app.core.security import OperatorContext
from app.ingestion.csv_reader import inspect_csv, read_csv_frame
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
from app.schemas.agent_ingestion import AgentSourceRole


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
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._tokenization_secret = tokenization_secret
        self._max_retries = max_retries
        self._governance = CsvGovernanceHandlers(
            output_root=output_root or Path("storage/exports/agent-targets")
        )
        self._rollback = CsvRollbackHandlers(
            output_root=output_root or Path("storage/exports/agent-targets")
        )
        self._csv_execution_enabled = csv_execution_enabled

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
        if action_kind == "aggregate_risk":
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

    async def _inspect_source(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
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

    async def _normalize_batch(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
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
                batches = await AgentBatchPlanner(session).create_for_run(
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
        work_ids = tuple(_resource_uuid(value, "work-item") for value in action.resource_ids)
        async with self._session_factory() as session:
            try:
                async with session.begin():
                    batch = await _find_exact_batch(
                        session,
                        run_id=context.run_id,
                        work_ids=work_ids,
                    )
                    repository = AgentAnalysisRepository(session)
                    claimed = await repository.claim_batch(
                        batch.id,
                        worker_id=context.worker_id,
                        run_lease_token=context.lease_token,
                        lease_seconds=60,
                    )
                    if claimed is None or claimed.lease_token is None:
                        raise RuntimeError("analysis batch is not claimable")
                    work_rows = await _work_rows(session, work_ids)
                    expected_kinds = {work.id: work.kind for work, _record in work_rows}
                    tools, runner, manifest_id = await self._analysis_runtime(
                        session,
                        context=context,
                        action=action,
                    )
                    result = await GraphIngestionAnalysisExecutors(
                        runner
                    ).analyze_actionable_batch(
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
                            input_payload=ReconcileEntityBatchInput(
                                task_id=context.task_id,
                                run_id=context.run_id,
                                phase=AgentPhase.ANALYZE_BATCHES,
                                evidence_refs=action.required_evidence,
                                work_items=tuple(
                                    IdentityWorkItem(
                                        work_item_id=work.id,
                                        entity_kind=record.entity_kind,
                                        target_locator=record.stable_locator,
                                        candidate_evidence_refs=(
                                            f"paired-record:{work.id}",
                                        ),
                                    )
                                    for work, record in work_rows
                                ),
                            ).model_dump(mode="json"),
                        ),
                        expected_work_item_kinds=expected_kinds,
                        allowed_evidence_refs=frozenset(action.required_evidence),
                    )
                    del tools
                    await repository.finalize_batch(
                        batch_id=claimed.id,
                        worker_id=context.worker_id,
                        run_lease_token=context.lease_token,
                        lease_token=claimed.lease_token,
                        output_hash="validated-graph-output",
                        findings=result.payloads,
                    )
            except GraphSubAgentFailure:
                await session.rollback()
                async with session.begin():
                    batch = await _find_exact_batch(
                        session,
                        run_id=context.run_id,
                        work_ids=work_ids,
                    )
                    if batch.status == "claimed":
                        batch.status = "pending"
                        batch.lease_owner = None
                        batch.lease_token = None
                        batch.lease_expires_at = None
                raise
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
                        "clarification_ids": [
                            str(item.id) for item in clarifications
                        ],
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
                current = await ExecutionRepository(
                    session
                ).current_target_version(context.task_id)
                stale = (
                    plan is not None
                    and (
                        current is None
                        or f"sha256:{current.file_sha256}"
                        != plan.target_version
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
                if not self._csv_execution_enabled:
                    raise RuntimeError(
                        "Agent graph CSV execution is disabled before a writable plan"
                    )

                async def execute_operation(operation_id: UUID) -> OperationOutcome:
                    record = await self._governance.execute_operation(
                        session,
                        _legacy_context(
                            context,
                            AgentPhase.EXECUTE_AND_VERIFY,
                        ),
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

                bound_action = action.model_copy(
                    update={
                        "resource_ids": (
                            f"execution-plan:{plan.id}",
                            *(f"operation:{item}" for item in operation_ids),
                        ),
                        "required_evidence": tuple(
                            f"execution-outcome:{item}"
                            for item in operation_ids
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
                terminal_state = (
                    "abnormal_input"
                    if context.current_node == "abnormal_input_report"
                    else "terminated"
                    if context.current_node == "termination_report"
                    else "completed"
                )
                fact_ref = (
                    f"report-facts:{context.run_id}:{context.graph_cursor}"
                )
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
                            outcome=terminal_state,
                            fact_refs=(fact_ref,),
                        ).model_dump(mode="json"),
                    ),
                    tenant_id=context.tenant_id,
                    kind="sync",
                    terminal_state=terminal_state,
                    facts=facts,
                    expected_rollback_eligible=rollback_eligible,
                )
        return _outcome(action)

    async def _plan_rollback(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                await self._rollback.plan(
                    session,
                    _legacy_context(context, AgentPhase.PLAN_RESTORE),
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
                assessment = await GraphRollbackAssessmentExecutor(
                    runner=runner
                ).run(
                    GraphSkillInvocation(
                        task_id=context.task_id,
                        run_id=context.run_id,
                        graph_run_id=context.graph_run_id,
                        graph_node=context.current_node,
                        graph_cursor=context.graph_cursor,
                        action_id=action.action_id,
                        evidence_manifest_id=manifest_id,
                        skill_name="assess-agent-rollback-impact",
                        skill_version="1.0.0",
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
                )
                if assessment.conflict_operation_ids:
                    await AgentGraphRepository(session).record_human_gate(
                        graph_run_id=context.graph_run_id,
                        cursor=context.graph_cursor,
                        gate_kind="rollback_conflict",
                        member_ids=tuple(
                            str(item)
                            for item in assessment.conflict_operation_ids
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
                            AgentHumanGateRecord.graph_run_id
                            == context.graph_run_id,
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
                    str(item["id"])
                    for item in task.agent_intent.get("operations", [])
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
                            AgentHumanGateRecord.graph_run_id
                            == context.graph_run_id,
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

    async def _execute_rollback(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        async with self._session_factory() as session:
            async with session.begin():
                task = await session.get(ReconciliationTask, context.task_id)
                if task is None or not task.agent_intent:
                    raise LookupError("rollback task facts are missing")
                parent = await session.get(
                    TargetVersionRecord,
                    UUID(str(task.agent_intent["target_version_id"])),
                )
                if parent is None:
                    raise LookupError("rollback target version is missing")
                operations = tuple(
                    _rollback_operation(
                        item,
                        target_version=f"sha256:{parent.file_sha256}",
                    )
                    for item in task.agent_intent.get("operations", [])
                )
                if not operations:
                    raise ValueError("rollback has no verified operations")
                operation_ids = tuple(item.id for item in operations)
                plan_id = uuid5(NAMESPACE_URL, f"agent-rollback:{task.id}")

                async def execute_operation(operation_id: UUID) -> OperationOutcome:
                    fact = await self._rollback.execute_operation(
                        session,
                        _legacy_context(
                            context,
                            AgentPhase.EXECUTE_RESTORE,
                        ),
                        operation_id,
                    )
                    status = _operation_status(str(fact["status"]))
                    return OperationOutcome(
                        operation_id=operation_id,
                        status=status,
                        verification_ref=(
                            f"verification:{operation_id}"
                            if status == "succeeded"
                            else None
                        ),
                        safe_error_code=(
                            None
                            if status == "succeeded"
                            else "rollback_target_write_failed"
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
                        skill_version="1.0.0",
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
                checkpoint = await AgentRuntimeRepository(session).get_checkpoint(
                    context.run_id,
                    phase=AgentPhase.EXECUTE_RESTORE,
                    checkpoint_key="agent-csv-rollback-execution-v1",
                )
                facts = (
                    dict(checkpoint.payload)
                    if checkpoint is not None
                    else {"mutations": []}
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
                    item.get("status") == "succeeded"
                    for item in facts.get("mutations", [])
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
        issued_sensitive_tokens = await tools.prepare_manifest_tokens(
            action.resource_ids
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
    if status == "succeeded":
        return "succeeded"
    if status == "failed":
        return "failed"
    if status == "blocked":
        return "blocked"
    return "skipped"


def _source_role(resource_id: str) -> str:
    parts = resource_id.split(":")
    if len(parts) != 4 or parts[0] != "source" or parts[1] not in {
        "authoritative",
        "target",
    }:
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
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

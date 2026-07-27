import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_graph.evidence import (
    EvidenceManifestV1,
    EvidenceMembershipError,
    require_manifest_evidence,
    require_manifest_resource,
    require_manifest_token,
)
from app.agent_graph.repository import AgentGraphRepository
from app.core.security import OperatorContext
from app.models.agent_graph import (
    AgentEvidenceManifestRecord,
    AgentGraphRunRecord,
    AgentSubAgentInvocationRecord,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask


class GraphToolAuthorizationError(PermissionError):
    pass


class GraphToolArgumentRejected(GraphToolAuthorizationError):
    """A model-supplied tool argument is outside the frozen evidence manifest."""

    def __init__(self, argument_name: str) -> None:
        super().__init__(
            f"evidence membership rejected for tool argument: {argument_name}"
        )
        self.repair_feedback = (
            {
                "path": f"tool_call.arguments.{argument_name}",
                "type": "value_not_in_evidence_manifest",
            },
        )


class GraphToolExecutionError(RuntimeError):
    pass


class GraphToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_id: str = Field(min_length=1, max_length=255)
    tenant_id: str = Field(min_length=1, max_length=128)
    task_id: UUID
    run_id: UUID
    graph_run_id: UUID
    graph_node: str = Field(min_length=1, max_length=128)
    graph_cursor: int = Field(ge=0)
    action_id: str = Field(min_length=1, max_length=128)
    evidence_manifest_id: UUID
    invocation_id: UUID
    allowed_tools: frozenset[str] = frozenset()


class GraphToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    payload: dict[str, Any]
    trace_id: str = Field(min_length=1, max_length=128)


GraphToolHandler = Callable[
    [GraphToolContext, Mapping[str, object]],
    Awaitable[dict[str, Any]],
]


_GOVERNANCE_EXECUTION_TOOL_NAMES = frozenset(
    {
        "read_execution_plan",
        "read_ready_operations",
        "request_execution_batch",
        "request_operation_execution",
        "read_operation_verification",
    }
)


GRAPH_NODE_TOOL_NAMES: dict[str, frozenset[str]] = {
    "inspect_sources": frozenset(
        {
            "inspect_configured_source",
            "read_connector_page",
            "submit_input_contract_verdict",
        }
    ),
    "normalize_input_batches": frozenset(
        {
            "read_connector_page",
            "submit_normalized_batch",
            "submit_input_marks",
            "submit_input_contract_verdict",
        }
    ),
    "analyze_actionable_batches": frozenset(
        {
            "read_work_item",
            "read_paired_record_evidence",
            "query_identity_postings",
            "read_claim_state",
            "submit_finding_batch",
        }
    ),
    "resolve_identity_conflicts": frozenset(
        {
            "read_frozen_conflict",
            "submit_conflict_interpretation",
        }
    ),
    "wait_high_risk_approvals": frozenset({"read_frozen_approval_group"}),
    "execute_ready_operations": _GOVERNANCE_EXECUTION_TOOL_NAMES,
    "execute_remaining_independent": _GOVERNANCE_EXECUTION_TOOL_NAMES,
    "generate_terminal_report": frozenset(
        {
            "read_report_fact_manifest",
            "submit_report_narrative",
        }
    ),
    "termination_report": frozenset(
        {
            "read_report_fact_manifest",
            "submit_report_narrative",
        }
    ),
    "abnormal_input_report": frozenset(
        {
            "read_report_fact_manifest",
            "submit_report_narrative",
        }
    ),
    "assess_restore_impact": frozenset(
        {
            "read_verified_mutations",
            "read_restore_comparison_facts",
            "submit_restore_assessment",
        }
    ),
    "execute_restore_operations": frozenset(
        {
            "read_execution_plan",
            "read_ready_operations",
            "request_operation_execution",
            "read_operation_verification",
        }
    ),
    "generate_rollback_report": frozenset(
        {
            "read_report_fact_manifest",
            "submit_report_narrative",
        }
    ),
}

_FORBIDDEN_ARGUMENT_KEYS = frozenset(
    {
        "credential",
        "credentials",
        "dsn",
        "filesystem_path",
        "path",
        "shell",
        "sql",
        "url",
    }
)


class GraphPhaseToolGateway:
    def __init__(
        self,
        session: AsyncSession,
        *,
        operator: OperatorContext,
        tools: Mapping[str, GraphToolHandler],
    ) -> None:
        self._session = session
        self._operator = operator
        self._tools = dict(tools)
        self._repository = AgentGraphRepository(session)

    async def call(
        self,
        tool_name: str,
        *,
        context: GraphToolContext,
        arguments: Mapping[str, object],
        resource_id: str | None = None,
        evidence_ref: str | None = None,
        sensitive_token: str | None = None,
    ) -> GraphToolResult:
        manifest = await self._authorize_durable_context(context)
        trace_id = str(uuid4())
        arguments_hash = _safe_hash(dict(arguments))
        try:
            self._authorize_call(
                tool_name,
                context=context,
                manifest=manifest,
                arguments=arguments,
                resource_id=resource_id,
                evidence_ref=evidence_ref,
                sensitive_token=sensitive_token,
            )
            handler = self._tools.get(tool_name)
            if handler is None:
                raise GraphToolAuthorizationError(
                    "phase tool has no registered server handler"
                )
        except GraphToolAuthorizationError as error:
            await self._repository.record_tool_call(
                invocation_id=context.invocation_id,
                tool_name=tool_name,
                arguments_hash=arguments_hash,
                result_hash=_safe_hash({"error": type(error).__name__}),
                authorized=False,
                status="denied",
                trace_id=trace_id,
            )
            raise
        try:
            payload = await handler(context, arguments)
            if not isinstance(payload, dict):
                raise ValueError("phase tool returned an invalid server payload")
        except Exception as error:
            await self._repository.record_tool_call(
                invocation_id=context.invocation_id,
                tool_name=tool_name,
                arguments_hash=arguments_hash,
                result_hash=_safe_hash({"error": type(error).__name__}),
                authorized=True,
                status="failed",
                trace_id=trace_id,
            )
            raise GraphToolExecutionError("authorized phase tool failed safely") from error
        await self._repository.record_tool_call(
            invocation_id=context.invocation_id,
            tool_name=tool_name,
            arguments_hash=arguments_hash,
            result_hash=_safe_hash(payload),
            authorized=True,
            status="completed",
            trace_id=trace_id,
        )
        return GraphToolResult(payload=payload, trace_id=trace_id)

    async def _authorize_durable_context(
        self,
        context: GraphToolContext,
    ) -> EvidenceManifestV1:
        if (
            context.operator_id != self._operator.operator_id
            or context.tenant_id != self._operator.tenant_id
        ):
            raise GraphToolAuthorizationError("operator context is not authorized")
        row = (
            await self._session.execute(
                select(
                    AgentSubAgentInvocationRecord,
                    AgentEvidenceManifestRecord,
                    AgentGraphRunRecord,
                    AgentRunRecord,
                    ReconciliationTask,
                )
                .join(
                    AgentEvidenceManifestRecord,
                    AgentEvidenceManifestRecord.id
                    == AgentSubAgentInvocationRecord.evidence_manifest_id,
                )
                .join(
                    AgentGraphRunRecord,
                    AgentGraphRunRecord.id
                    == AgentSubAgentInvocationRecord.graph_run_id,
                )
                .join(
                    AgentRunRecord,
                    AgentRunRecord.id == AgentGraphRunRecord.run_id,
                )
                .join(
                    ReconciliationTask,
                    ReconciliationTask.id == AgentRunRecord.task_id,
                )
                .where(AgentSubAgentInvocationRecord.id == context.invocation_id)
            )
        ).one_or_none()
        if row is None:
            raise GraphToolAuthorizationError("Agent graph invocation is not authorized")
        invocation, manifest_record, graph_run, run, task = row
        if (
            invocation.id != context.invocation_id
            or invocation.action_id != context.action_id
            or invocation.evidence_manifest_id != context.evidence_manifest_id
            or manifest_record.graph_run_id != context.graph_run_id
            or manifest_record.graph_node != context.graph_node
            or manifest_record.cursor != context.graph_cursor
            or manifest_record.action_id != context.action_id
            or graph_run.id != context.graph_run_id
            or graph_run.cursor != context.graph_cursor
            or graph_run.current_node != context.graph_node
            or run.id != context.run_id
            or run.task_id != context.task_id
            or run.tenant_id != context.tenant_id
            or task.tenant_id != context.tenant_id
            or run.workflow_version != "agent-graph-v1"
            or task.workflow_version != "agent-graph-v1"
        ):
            raise GraphToolAuthorizationError(
                "Agent graph tool context does not match durable state"
            )
        manifest = EvidenceManifestV1.model_validate(manifest_record.manifest)
        if (
            str(manifest.manifest_id) != str(manifest_record.id)
            or manifest.content_hash != manifest_record.content_hash
            or manifest.task_id != str(context.task_id)
            or manifest.run_id != str(context.run_id)
        ):
            raise GraphToolAuthorizationError("evidence manifest is not authorized")
        return manifest

    @staticmethod
    def _authorize_call(
        tool_name: str,
        *,
        context: GraphToolContext,
        manifest: EvidenceManifestV1,
        arguments: Mapping[str, object],
        resource_id: str | None,
        evidence_ref: str | None,
        sensitive_token: str | None,
    ) -> None:
        allowed_for_node = GRAPH_NODE_TOOL_NAMES.get(context.graph_node, frozenset())
        if tool_name not in context.allowed_tools or tool_name not in allowed_for_node:
            raise GraphToolAuthorizationError("phase tool is not authorized")
        if _contains_forbidden_argument(arguments):
            raise GraphToolAuthorizationError(
                "arbitrary connector/tool arguments are forbidden"
            )
        if resource_id is not None:
            try:
                require_manifest_resource(manifest, resource_id)
            except EvidenceMembershipError as error:
                raise GraphToolArgumentRejected("resource_id") from error
        if evidence_ref is not None:
            try:
                require_manifest_evidence(manifest, evidence_ref)
            except EvidenceMembershipError as error:
                raise GraphToolArgumentRejected("evidence_ref") from error
        if sensitive_token is not None:
            try:
                require_manifest_token(manifest, sensitive_token)
            except EvidenceMembershipError as error:
                raise GraphToolArgumentRejected("sensitive_token") from error


def _contains_forbidden_argument(value: object, *, field: str | None = None) -> bool:
    if field is not None and field.casefold() in _FORBIDDEN_ARGUMENT_KEYS:
        return True
    if isinstance(value, Mapping):
        return any(
            _contains_forbidden_argument(item, field=str(key))
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_argument(item) for item in value)
    return False


def _safe_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

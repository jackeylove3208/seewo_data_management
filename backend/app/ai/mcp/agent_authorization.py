from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.agent_runtime.state_machine import AgentPhase


class AgentToolAuthorizationError(PermissionError):
    pass


class AgentCapability(StrEnum):
    READ_CONNECTOR_PAGE = "read_connector_page"
    PERSIST_NORMALIZED_INPUT = "persist_normalized_input"
    READ_IDENTITY_EVIDENCE = "read_identity_evidence"
    PERSIST_FINDING = "persist_finding"
    READ_CONFLICT = "read_conflict"
    PERSIST_DECISION = "persist_decision"
    READ_APPROVAL = "read_approval"
    PERSIST_APPROVAL = "persist_approval"
    EXECUTE_TARGET_OPERATION = "execute_target_operation"
    VERIFY_TARGET_OPERATION = "verify_target_operation"
    READ_REPORT_FACTS = "read_report_facts"
    PERSIST_REPORT = "persist_report"


PHASE_CAPABILITIES: dict[AgentPhase, frozenset[AgentCapability]] = {
    AgentPhase.INGEST_AND_NORMALIZE: frozenset(
        {AgentCapability.READ_CONNECTOR_PAGE, AgentCapability.PERSIST_NORMALIZED_INPUT}
    ),
    AgentPhase.BUILD_IDENTITY_WORK: frozenset({AgentCapability.READ_IDENTITY_EVIDENCE}),
    AgentPhase.ANALYZE_BATCHES: frozenset(
        {AgentCapability.READ_IDENTITY_EVIDENCE, AgentCapability.PERSIST_FINDING}
    ),
    AgentPhase.CLARIFY_IDENTITY_CONFLICTS: frozenset(
        {AgentCapability.READ_CONFLICT, AgentCapability.PERSIST_DECISION}
    ),
    AgentPhase.AGGREGATE_RISK_AND_APPROVALS: frozenset(
        {AgentCapability.READ_APPROVAL, AgentCapability.PERSIST_APPROVAL}
    ),
    AgentPhase.EXECUTE_AND_VERIFY: frozenset(
        {AgentCapability.EXECUTE_TARGET_OPERATION, AgentCapability.VERIFY_TARGET_OPERATION}
    ),
    AgentPhase.GENERATE_REPORT: frozenset(
        {AgentCapability.READ_REPORT_FACTS, AgentCapability.PERSIST_REPORT}
    ),
    AgentPhase.PLAN_RESTORE: frozenset({AgentCapability.READ_REPORT_FACTS}),
    AgentPhase.EXECUTE_RESTORE: frozenset(
        {AgentCapability.EXECUTE_TARGET_OPERATION, AgentCapability.VERIFY_TARGET_OPERATION}
    ),
    AgentPhase.REPORT_RESTORE: frozenset(
        {AgentCapability.READ_REPORT_FACTS, AgentCapability.PERSIST_REPORT}
    ),
}


class AgentToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_id: str = Field(min_length=1, max_length=255)
    tenant_id: str = Field(min_length=1, max_length=128)
    conversation_id: UUID | None = None
    task_id: UUID
    run_id: UUID
    phase: AgentPhase
    snapshot_ids: frozenset[UUID] = frozenset()
    plan_version: int | None = Field(default=None, ge=1)
    approval_id: UUID | None = None
    allowed_capabilities: frozenset[AgentCapability] = frozenset()
    allowed_resource_ids: frozenset[UUID] = frozenset()


def require_agent_capability(
    context: AgentToolContext,
    capability: AgentCapability,
) -> None:
    phase_capabilities = PHASE_CAPABILITIES.get(context.phase, frozenset())
    if capability not in context.allowed_capabilities or capability not in phase_capabilities:
        raise AgentToolAuthorizationError("capability not authorized")


def require_agent_resource(context: AgentToolContext, resource_id: UUID) -> None:
    if resource_id not in context.allowed_resource_ids:
        raise AgentToolAuthorizationError("resource not authorized")

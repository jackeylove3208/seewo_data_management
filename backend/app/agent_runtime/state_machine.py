from dataclasses import dataclass
from enum import StrEnum


class AgentRunKind(StrEnum):
    SYNC = "sync"
    ROLLBACK = "rollback"


class AgentPhase(StrEnum):
    INTENT_CONFIRMED = "intent_confirmed"
    ACQUIRE_SCHOOL_LOCK = "acquire_school_lock"
    INGEST_AND_NORMALIZE = "ingest_and_normalize"
    BUILD_IDENTITY_WORK = "build_identity_work"
    ANALYZE_BATCHES = "analyze_batches"
    CLARIFY_IDENTITY_CONFLICTS = "clarify_identity_conflicts"
    AGGREGATE_RISK_AND_APPROVALS = "aggregate_risk_and_approvals"
    COMPILE_EXECUTION_PLAN = "compile_execution_plan"
    EXECUTE_AND_VERIFY = "execute_and_verify"
    GENERATE_REPORT = "generate_report"
    PLAN_RESTORE = "plan_restore"
    CLARIFY_RESTORE_CONFLICTS = "clarify_restore_conflicts"
    APPROVE_RESTORE = "approve_restore"
    EXECUTE_RESTORE = "execute_restore"
    REPORT_RESTORE = "report_restore"
    TERMINAL = "terminal"


class AgentRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    BLOCKED_MODEL_ERROR = "blocked_model_error"
    TERMINATING = "terminating"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    FAILED = "failed"


TERMINAL_STATUSES = frozenset(
    {AgentRunStatus.COMPLETED, AgentRunStatus.TERMINATED, AgentRunStatus.FAILED}
)

STATUS_TRANSITIONS: dict[AgentRunStatus, frozenset[AgentRunStatus]] = {
    AgentRunStatus.PENDING: frozenset({AgentRunStatus.RUNNING, AgentRunStatus.TERMINATING}),
    AgentRunStatus.RUNNING: frozenset(
        {
            AgentRunStatus.WAITING_HUMAN,
            AgentRunStatus.BLOCKED_MODEL_ERROR,
            AgentRunStatus.TERMINATING,
        }
    ),
    AgentRunStatus.WAITING_HUMAN: frozenset({AgentRunStatus.RUNNING, AgentRunStatus.TERMINATING}),
    AgentRunStatus.BLOCKED_MODEL_ERROR: frozenset({AgentRunStatus.TERMINATING}),
    AgentRunStatus.TERMINATING: frozenset({AgentRunStatus.TERMINATED}),
}

PHASE_ADVANCE_STATUSES = frozenset({AgentRunStatus.PENDING, AgentRunStatus.RUNNING})

SYNC_PHASES = (
    AgentPhase.INTENT_CONFIRMED,
    AgentPhase.ACQUIRE_SCHOOL_LOCK,
    AgentPhase.INGEST_AND_NORMALIZE,
    AgentPhase.BUILD_IDENTITY_WORK,
    AgentPhase.ANALYZE_BATCHES,
    AgentPhase.CLARIFY_IDENTITY_CONFLICTS,
    AgentPhase.AGGREGATE_RISK_AND_APPROVALS,
    AgentPhase.COMPILE_EXECUTION_PLAN,
    AgentPhase.EXECUTE_AND_VERIFY,
    AgentPhase.GENERATE_REPORT,
    AgentPhase.TERMINAL,
)

ROLLBACK_PHASES = (
    AgentPhase.INTENT_CONFIRMED,
    AgentPhase.ACQUIRE_SCHOOL_LOCK,
    AgentPhase.PLAN_RESTORE,
    AgentPhase.CLARIFY_RESTORE_CONFLICTS,
    AgentPhase.APPROVE_RESTORE,
    AgentPhase.EXECUTE_RESTORE,
    AgentPhase.REPORT_RESTORE,
    AgentPhase.TERMINAL,
)


class InvalidAgentTransition(ValueError):
    pass


@dataclass(frozen=True)
class AgentTransition:
    phase: AgentPhase
    status: AgentRunStatus


def transition(
    *,
    kind: AgentRunKind,
    current_phase: AgentPhase,
    current_status: AgentRunStatus,
    requested_phase: AgentPhase | None = None,
    requested_status: AgentRunStatus | None = None,
) -> AgentTransition:
    if current_status in TERMINAL_STATUSES or current_phase is AgentPhase.TERMINAL:
        raise InvalidAgentTransition("terminal agent runs are immutable")
    if requested_phase is not None and requested_status is not None:
        raise InvalidAgentTransition("change phase or status in one transition, not both")
    if requested_status is not None:
        if requested_status not in STATUS_TRANSITIONS.get(current_status, frozenset()):
            raise InvalidAgentTransition("illegal agent status transition")
        return AgentTransition(current_phase, requested_status)
    if requested_phase is None:
        raise InvalidAgentTransition("an agent transition target is required")
    if current_status not in PHASE_ADVANCE_STATUSES:
        raise InvalidAgentTransition(f"agent status {current_status.value} cannot advance phase")

    phases = SYNC_PHASES if kind is AgentRunKind.SYNC else ROLLBACK_PHASES
    try:
        current_index = phases.index(current_phase)
    except ValueError as error:
        raise InvalidAgentTransition("current phase does not belong to run kind") from error
    abnormal_input_jump = (
        kind is AgentRunKind.SYNC
        and current_phase is AgentPhase.INGEST_AND_NORMALIZE
        and requested_phase is AgentPhase.GENERATE_REPORT
    )
    if not abnormal_input_jump and (
        current_index + 1 >= len(phases) or phases[current_index + 1] is not requested_phase
    ):
        raise InvalidAgentTransition(
            f"illegal agent transition: {current_phase.value} -> {requested_phase.value}"
        )
    status = (
        AgentRunStatus.COMPLETED
        if requested_phase is AgentPhase.TERMINAL
        else AgentRunStatus.RUNNING
    )
    return AgentTransition(requested_phase, status)

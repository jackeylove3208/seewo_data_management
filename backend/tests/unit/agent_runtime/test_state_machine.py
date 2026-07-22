import pytest

from app.agent_runtime.state_machine import (
    AgentPhase,
    AgentRunKind,
    AgentRunStatus,
    InvalidAgentTransition,
    transition,
)


def test_sync_run_advances_only_to_the_next_server_owned_phase() -> None:
    result = transition(
        kind=AgentRunKind.SYNC,
        current_phase=AgentPhase.INTENT_CONFIRMED,
        current_status=AgentRunStatus.PENDING,
        requested_phase=AgentPhase.ACQUIRE_SCHOOL_LOCK,
    )

    assert result.phase is AgentPhase.ACQUIRE_SCHOOL_LOCK
    assert result.status is AgentRunStatus.RUNNING


def test_sync_run_cannot_skip_analysis_and_approvals() -> None:
    with pytest.raises(InvalidAgentTransition, match="illegal agent transition"):
        transition(
            kind=AgentRunKind.SYNC,
            current_phase=AgentPhase.INGEST_AND_NORMALIZE,
            current_status=AgentRunStatus.RUNNING,
            requested_phase=AgentPhase.EXECUTE_AND_VERIFY,
        )


def test_model_failure_blocks_without_releasing_or_advancing() -> None:
    result = transition(
        kind=AgentRunKind.SYNC,
        current_phase=AgentPhase.ANALYZE_BATCHES,
        current_status=AgentRunStatus.RUNNING,
        requested_status=AgentRunStatus.BLOCKED_MODEL_ERROR,
    )

    assert result.phase is AgentPhase.ANALYZE_BATCHES
    assert result.status is AgentRunStatus.BLOCKED_MODEL_ERROR


@pytest.mark.parametrize(
    ("requested_phase", "requested_status"),
    [
        (AgentPhase.CLARIFY_IDENTITY_CONFLICTS, None),
        (None, AgentRunStatus.RUNNING),
        (None, AgentRunStatus.WAITING_HUMAN),
    ],
)
def test_model_failure_can_only_enter_termination(
    requested_phase, requested_status
) -> None:
    with pytest.raises(InvalidAgentTransition, match="status transition|advance"):
        transition(
            kind=AgentRunKind.SYNC,
            current_phase=AgentPhase.ANALYZE_BATCHES,
            current_status=AgentRunStatus.BLOCKED_MODEL_ERROR,
            requested_phase=requested_phase,
            requested_status=requested_status,
        )

    terminated = transition(
        kind=AgentRunKind.SYNC,
        current_phase=AgentPhase.ANALYZE_BATCHES,
        current_status=AgentRunStatus.BLOCKED_MODEL_ERROR,
        requested_status=AgentRunStatus.TERMINATING,
    )
    assert terminated.status is AgentRunStatus.TERMINATING


def test_terminal_run_is_immutable() -> None:
    with pytest.raises(InvalidAgentTransition, match="terminal"):
        transition(
            kind=AgentRunKind.SYNC,
            current_phase=AgentPhase.TERMINAL,
            current_status=AgentRunStatus.COMPLETED,
            requested_phase=AgentPhase.INTENT_CONFIRMED,
        )

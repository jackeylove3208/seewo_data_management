from uuid import uuid4

import pytest

from app.agent_graph.guards import (
    GraphGuardRejected,
    GraphGuardService,
    ReplanDisposition,
)


def test_same_node_replan_is_bounded_to_three_automatic_retries() -> None:
    guard = GraphGuardService()

    assert guard.replan_disposition(replan_count=0, cross_phase=False) is (
        ReplanDisposition.AUTO_ALLOWED
    )
    assert guard.replan_disposition(replan_count=2, cross_phase=False) is (
        ReplanDisposition.AUTO_ALLOWED
    )
    assert guard.replan_disposition(replan_count=3, cross_phase=False) is (
        ReplanDisposition.MODEL_ERROR_BLOCKED
    )


def test_cross_phase_replan_always_requires_a_human_gate() -> None:
    assert GraphGuardService().replan_disposition(
        replan_count=0,
        cross_phase=True,
    ) is ReplanDisposition.HUMAN_GATE_REQUIRED


def test_stale_fencing_context_is_rejected_before_commit() -> None:
    with pytest.raises(GraphGuardRejected, match="stale_fencing"):
        GraphGuardService().validate_fencing(
            expected_worker_id="worker-old",
            expected_lease_token=uuid4(),
            expected_attempt_count=1,
            persisted_worker_id="worker-new",
            persisted_lease_token=uuid4(),
            persisted_attempt_count=2,
        )


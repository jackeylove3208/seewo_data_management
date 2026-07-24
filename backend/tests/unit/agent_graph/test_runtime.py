from uuid import uuid4

from app.agent_graph.contracts import SingleActionReasonCode
from app.agent_graph.runtime import (
    _action,
    _single_action_reason,
    production_candidate_templates,
)
from app.agent_graph.worker import GraphWorkContext


def test_production_runtime_exposes_real_source_inspection_choices() -> None:
    templates = production_candidate_templates("inspect_sources")

    assert {item.action_id for item in templates} == {
        "inspect_authority",
        "inspect_target",
        "normalize_ready_sources",
    }
    assert {item.graph_action_kind for item in templates} == {
        "inspect_authority",
        "inspect_target",
        "normalize_ready_sources",
    }


def test_production_runtime_has_a_guarded_action_for_every_non_terminal_node() -> None:
    from app.agent_graph.definition import SYNC_GRAPH_V1

    missing = {
        node.node_id
        for node in SYNC_GRAPH_V1.nodes
        if node.node_id != "terminal"
        and not production_candidate_templates(node.node_id)
    }

    assert missing == set()


def test_single_action_reason_uses_semantic_server_fact() -> None:
    context = GraphWorkContext(
        worker_id="worker",
        run_id=uuid4(),
        task_id=uuid4(),
        tenant_id="school-1",
        graph_run_id=uuid4(),
        graph_version="agent-sync-graph-v1",
        current_node="inspect_sources",
        graph_cursor=1,
        attempt_count=1,
        lease_token=uuid4(),
    )

    assert _single_action_reason(
        context,
        (
            _action(
                "terminate_requested",
                successor="drain_current_atomic_unit",
                kind="terminate",
            ),
        ),
    ) is SingleActionReasonCode.TERMINATION_REQUESTED
    assert _single_action_reason(
        context,
        (
            _action(
                "wait_for_operator",
                successor="wait_high_risk_approvals",
                kind="wait_human",
                requires_human=True,
            ),
        ),
    ) is SingleActionReasonCode.HUMAN_GATE_REQUIRED
    assert _single_action_reason(
        context,
        (_action("finish", successor="terminal"),),
    ) is SingleActionReasonCode.TERMINALIZATION_REQUIRED

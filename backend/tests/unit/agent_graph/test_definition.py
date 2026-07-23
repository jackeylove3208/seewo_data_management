from app.agent_graph.definition import (
    GraphNodeKind,
    get_graph_definition,
)


def test_sync_graph_contains_the_controlled_design_nodes() -> None:
    graph = get_graph_definition("agent-sync-graph-v1")

    assert graph.initial_node == "intent_confirmed"
    assert graph.node("inspect_sources").kind is GraphNodeKind.DECISION
    assert graph.node("normalize_input_batches").kind is GraphNodeKind.DECISION
    assert graph.node("analyze_actionable_batches").kind is GraphNodeKind.DECISION
    assert graph.node("wait_high_risk_approvals").kind is GraphNodeKind.HUMAN_GATE
    assert graph.node("terminal").kind is GraphNodeKind.TERMINAL


def test_preflight_has_two_real_paths_instead_of_a_wrapped_next_phase() -> None:
    graph = get_graph_definition("agent-sync-graph-v1")
    templates = graph.node("preflight_execution").action_templates

    assert {template.action_kind for template in templates} == {
        "request_cross_phase_replan",
        "execute_ready_operations",
    }
    assert {template.successor_node for template in templates} == {
        "wait_replan_confirmation",
        "execute_ready_operations",
    }


def test_rollback_graph_is_versioned_separately() -> None:
    graph = get_graph_definition("agent-rollback-graph-v1")

    assert graph.initial_node == "rollback_intent_confirmed"
    assert graph.node("wait_rollback_approval").kind is GraphNodeKind.HUMAN_GATE
    assert graph.node("generate_rollback_report").kind is GraphNodeKind.REPORT


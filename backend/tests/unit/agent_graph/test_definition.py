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


def test_sync_graph_v2_materializes_before_inspection() -> None:
    graph = get_graph_definition("agent-sync-graph-v2")

    assert {
        (item.action_kind, item.successor_node)
        for item in graph.node("acquire_school_lock").action_templates
    } == {("materialize_sources", "materialize_sources")}
    assert graph.node("materialize_sources").kind is GraphNodeKind.DETERMINISTIC
    assert {
        (item.action_kind, item.successor_node)
        for item in graph.node("materialize_sources").action_templates
    } == {("materialize_remote_authority", "inspect_sources")}


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


def test_analysis_enters_risk_aggregation_without_reusing_side_effect_action() -> None:
    graph = get_graph_definition("agent-sync-graph-v1")
    analysis_actions = {
        (template.action_kind, template.successor_node)
        for template in graph.node("analyze_actionable_batches").action_templates
    }
    conflict_actions = {
        (template.action_kind, template.successor_node)
        for template in graph.node("resolve_identity_conflicts").action_templates
    }

    assert ("enter_aggregate_risk", "aggregate_risk") in analysis_actions
    assert ("aggregate_risk", "aggregate_risk") not in analysis_actions
    assert conflict_actions == {("enter_aggregate_risk", "aggregate_risk")}


def test_rollback_graph_is_versioned_separately() -> None:
    graph = get_graph_definition("agent-rollback-graph-v1")

    assert graph.initial_node == "rollback_intent_confirmed"
    assert graph.node("wait_rollback_approval").kind is GraphNodeKind.HUMAN_GATE
    assert graph.node("generate_rollback_report").kind is GraphNodeKind.REPORT
    conflict_wait = graph.node("wait_restore_conflicts")
    assert {
        (item.action_kind, item.successor_node)
        for item in conflict_wait.action_templates
    } == {("wait_rollback_approval", "wait_rollback_approval")}


def test_every_non_terminal_graph_node_declares_at_least_one_legal_action() -> None:
    for graph_version in (
        "agent-sync-graph-v1",
        "agent-sync-graph-v2",
        "agent-rollback-graph-v1",
    ):
        graph = get_graph_definition(graph_version)
        missing = {
            node.node_id
            for node in graph.nodes
            if node.kind is not GraphNodeKind.TERMINAL and not node.action_templates
        }
        assert missing == set()

from uuid import uuid4

import pytest

import app.agent_graph.runtime as graph_runtime
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


def test_sync_v2_exposes_only_the_materialization_action_at_its_new_node() -> None:
    templates = production_candidate_templates(
        "materialize_sources",
        graph_version="agent-sync-graph-v2",
    )

    assert [item.action_id for item in templates] == [
        "materialize_remote_authority"
    ]
    assert templates[0].successor_node == "inspect_sources"


def test_unknown_graph_version_is_rejected_instead_of_using_rollback_actions() -> None:
    with pytest.raises(ValueError, match="unsupported Agent graph version"):
        production_candidate_templates(
            "load_verified_mutations",
            graph_version="unknown-graph-v9",
        )


def test_production_runtime_has_a_guarded_action_for_every_non_terminal_node() -> None:
    from app.agent_graph.definition import SYNC_GRAPH_V1

    missing = {
        node.node_id
        for node in SYNC_GRAPH_V1.nodes
        if node.node_id != "terminal" and not production_candidate_templates(node.node_id)
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

    assert (
        _single_action_reason(
            context,
            (
                _action(
                    "terminate_requested",
                    successor="drain_current_atomic_unit",
                    kind="terminate",
                ),
            ),
        )
        is SingleActionReasonCode.TERMINATION_REQUESTED
    )
    assert (
        _single_action_reason(
            context,
            (
                _action(
                    "wait_for_operator",
                    successor="wait_high_risk_approvals",
                    kind="wait_human",
                    requires_human=True,
                ),
            ),
        )
        is SingleActionReasonCode.HUMAN_GATE_REQUIRED
    )
    assert (
        _single_action_reason(
            context,
            (_action("finish", successor="terminal"),),
        )
        is SingleActionReasonCode.TERMINALIZATION_REQUIRED
    )


def test_ready_governance_operations_are_dispatched_as_one_bounded_batch() -> None:
    context = GraphWorkContext(
        worker_id="worker",
        run_id=uuid4(),
        task_id=uuid4(),
        tenant_id="school-1",
        graph_run_id=uuid4(),
        graph_version="agent-sync-graph-v1",
        current_node="execute_ready_operations",
        graph_cursor=20,
        attempt_count=1,
        lease_token=uuid4(),
    )
    plan_id = uuid4()
    operation_ids = tuple(uuid4() for _index in range(51))

    action = graph_runtime._execution_batch_action(
        context,
        plan_id=plan_id,
        operation_ids=operation_ids,
    )

    assert action.action_id == "execute_operations_batch"
    assert action.graph_action_kind == "verify_operations"
    assert action.resource_ids == (
        f"execution-plan:{plan_id}",
        *(f"operation:{item}" for item in operation_ids[:50]),
    )
    assert action.required_evidence == tuple(
        f"execution-outcome:{item}" for item in operation_ids[:50]
    )


def test_execution_v2_batch_is_server_deterministic() -> None:
    context = GraphWorkContext(
        worker_id="worker",
        run_id=uuid4(),
        task_id=uuid4(),
        tenant_id="school-1",
        graph_run_id=uuid4(),
        graph_version="agent-sync-graph-v1",
        current_node="execute_ready_operations",
        graph_cursor=20,
        attempt_count=1,
        lease_token=uuid4(),
        execution_contract_version="deterministic-execution-v2",
    )

    action = graph_runtime._execution_batch_action(
        context,
        plan_id=uuid4(),
        operation_ids=(uuid4(),),
    )

    assert action.kind == "run_deterministic"
    assert action.sub_agent is None

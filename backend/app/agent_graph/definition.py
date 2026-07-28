from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GraphNodeKind(StrEnum):
    DECISION = "decision"
    SUB_AGENT = "sub_agent"
    DETERMINISTIC = "deterministic"
    HUMAN_GATE = "human_gate"
    REPORT = "report"
    TERMINAL = "terminal"


class GraphActionTemplateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_kind: str = Field(min_length=1, max_length=128)
    successor_node: str = Field(min_length=1, max_length=128)


class GraphNodeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1, max_length=128)
    kind: GraphNodeKind
    action_templates: tuple[GraphActionTemplateV1, ...] = ()


class GraphDefinitionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_version: str = Field(min_length=1, max_length=128)
    initial_node: str = Field(min_length=1, max_length=128)
    nodes: tuple[GraphNodeV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> "GraphDefinitionV1":
        node_ids = tuple(node.node_id for node in self.nodes)
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("graph node IDs must be unique")
        if self.initial_node not in node_ids:
            raise ValueError("initial graph node does not exist")
        known_nodes = set(node_ids)
        for node in self.nodes:
            for template in node.action_templates:
                if template.successor_node not in known_nodes:
                    raise ValueError(
                        f"unknown successor node: {template.successor_node}"
                    )
        return self

    def node(self, node_id: str) -> GraphNodeV1:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(node_id)


def _node(
    node_id: str,
    kind: GraphNodeKind,
    *actions: tuple[str, str],
) -> GraphNodeV1:
    return GraphNodeV1(
        node_id=node_id,
        kind=kind,
        action_templates=tuple(
            GraphActionTemplateV1(action_kind=action, successor_node=successor)
            for action, successor in actions
        ),
    )


SYNC_GRAPH_V1 = GraphDefinitionV1(
    graph_version="agent-sync-graph-v1",
    initial_node="intent_confirmed",
    nodes=(
        _node(
            "intent_confirmed",
            GraphNodeKind.DETERMINISTIC,
            ("acquire_school_lock", "acquire_school_lock"),
        ),
        _node(
            "acquire_school_lock",
            GraphNodeKind.DETERMINISTIC,
            ("inspect_sources", "inspect_sources"),
        ),
        _node(
            "inspect_sources",
            GraphNodeKind.DECISION,
            ("inspect_authority", "inspect_sources"),
            ("inspect_target", "inspect_sources"),
            ("normalize_ready_sources", "normalize_input_batches"),
        ),
        _node(
            "normalize_input_batches",
            GraphNodeKind.DECISION,
            ("normalize_next_batch", "normalize_input_batches"),
            ("validate_normalized_input", "validate_input_contract"),
        ),
        _node(
            "validate_input_contract",
            GraphNodeKind.DETERMINISTIC,
            ("report_abnormal_input", "abnormal_input_report"),
            ("build_identity_index", "build_identity_index"),
        ),
        _node(
            "abnormal_input_report",
            GraphNodeKind.REPORT,
            ("finish_abnormal_report", "terminal"),
        ),
        _node(
            "build_identity_index",
            GraphNodeKind.DETERMINISTIC,
            ("build_identity_index", "construct_identity_work"),
        ),
        _node(
            "construct_identity_work",
            GraphNodeKind.DETERMINISTIC,
            ("construct_identity_work", "analyze_actionable_batches"),
        ),
        _node(
            "analyze_actionable_batches",
            GraphNodeKind.DECISION,
            ("analyze_next_batch", "analyze_actionable_batches"),
            ("repair_analysis_batch", "repair_analysis_batch"),
            ("resolve_identity_conflicts", "resolve_identity_conflicts"),
            ("enter_aggregate_risk", "aggregate_risk"),
        ),
        _node(
            "repair_analysis_batch",
            GraphNodeKind.SUB_AGENT,
            ("repair_analysis_batch", "analyze_actionable_batches"),
        ),
        _node(
            "resolve_identity_conflicts",
            GraphNodeKind.SUB_AGENT,
            ("enter_aggregate_risk", "aggregate_risk"),
        ),
        _node(
            "aggregate_risk",
            GraphNodeKind.DETERMINISTIC,
            ("aggregate_risk", "wait_high_risk_approvals"),
        ),
        _node(
            "wait_high_risk_approvals",
            GraphNodeKind.HUMAN_GATE,
            ("compile_execution_plan", "compile_execution_plan"),
        ),
        _node(
            "compile_execution_plan",
            GraphNodeKind.DETERMINISTIC,
            ("preflight_execution", "preflight_execution"),
        ),
        _node(
            "preflight_execution",
            GraphNodeKind.DECISION,
            ("request_cross_phase_replan", "wait_replan_confirmation"),
            ("execute_ready_operations", "execute_ready_operations"),
        ),
        _node(
            "wait_replan_confirmation",
            GraphNodeKind.HUMAN_GATE,
            ("compile_execution_plan", "compile_execution_plan"),
        ),
        _node(
            "execute_ready_operations",
            GraphNodeKind.DECISION,
            ("verify_operations", "verify_operations"),
        ),
        _node(
            "verify_operations",
            GraphNodeKind.DECISION,
            ("execute_remaining_independent", "execute_remaining_independent"),
            ("generate_terminal_report", "generate_terminal_report"),
        ),
        _node(
            "execute_remaining_independent",
            GraphNodeKind.DECISION,
            ("verify_operations", "verify_operations"),
        ),
        _node(
            "generate_terminal_report",
            GraphNodeKind.REPORT,
            ("finish_terminal_report", "terminal"),
        ),
        _node(
            "drain_current_atomic_unit",
            GraphNodeKind.DETERMINISTIC,
            ("termination_report", "termination_report"),
        ),
        _node(
            "termination_report",
            GraphNodeKind.REPORT,
            ("finish_termination_report", "terminal"),
        ),
        _node(
            "blocked_model_error",
            GraphNodeKind.HUMAN_GATE,
            ("terminate_blocked_run", "drain_current_atomic_unit"),
        ),
        _node("terminal", GraphNodeKind.TERMINAL),
    ),
)


SYNC_GRAPH_V2 = GraphDefinitionV1(
    graph_version="agent-sync-graph-v2",
    initial_node=SYNC_GRAPH_V1.initial_node,
    nodes=tuple(
        (
            _node(
                "acquire_school_lock",
                GraphNodeKind.DETERMINISTIC,
                ("materialize_sources", "materialize_sources"),
            )
            if node.node_id == "acquire_school_lock"
            else node
        )
        for node in SYNC_GRAPH_V1.nodes
        if node.node_id != "terminal"
    )
    + (
        _node(
            "materialize_sources",
            GraphNodeKind.DETERMINISTIC,
            ("materialize_remote_authority", "inspect_sources"),
        ),
        SYNC_GRAPH_V1.node("terminal"),
    ),
)


ROLLBACK_GRAPH_V1 = GraphDefinitionV1(
    graph_version="agent-rollback-graph-v1",
    initial_node="rollback_intent_confirmed",
    nodes=(
        _node(
            "rollback_intent_confirmed",
            GraphNodeKind.DETERMINISTIC,
            ("acquire_school_lock", "acquire_school_lock"),
        ),
        _node(
            "acquire_school_lock",
            GraphNodeKind.DETERMINISTIC,
            ("load_verified_mutations", "load_verified_mutations"),
        ),
        _node(
            "load_verified_mutations",
            GraphNodeKind.DETERMINISTIC,
            ("assess_restore_impact", "assess_restore_impact"),
        ),
        _node(
            "assess_restore_impact",
            GraphNodeKind.SUB_AGENT,
            ("assess_restore_impact", "wait_restore_conflicts"),
        ),
        _node(
            "wait_restore_conflicts",
            GraphNodeKind.HUMAN_GATE,
            ("wait_rollback_approval", "wait_rollback_approval"),
        ),
        _node(
            "wait_rollback_approval",
            GraphNodeKind.HUMAN_GATE,
            ("compile_restore_plan", "compile_restore_plan"),
        ),
        _node(
            "compile_restore_plan",
            GraphNodeKind.DETERMINISTIC,
            ("preflight_restore", "preflight_restore"),
        ),
        _node(
            "preflight_restore",
            GraphNodeKind.DETERMINISTIC,
            ("execute_restore_operations", "execute_restore_operations"),
        ),
        _node(
            "execute_restore_operations",
            GraphNodeKind.SUB_AGENT,
            ("verify_restore_operations", "verify_restore_operations"),
        ),
        _node(
            "verify_restore_operations",
            GraphNodeKind.DETERMINISTIC,
            ("generate_rollback_report", "generate_rollback_report"),
        ),
        _node(
            "generate_rollback_report",
            GraphNodeKind.REPORT,
            ("finish_rollback_report", "terminal"),
        ),
        _node(
            "drain_current_atomic_unit",
            GraphNodeKind.DETERMINISTIC,
            ("termination_report", "termination_report"),
        ),
        _node(
            "termination_report",
            GraphNodeKind.REPORT,
            ("finish_termination_report", "terminal"),
        ),
        _node(
            "blocked_model_error",
            GraphNodeKind.HUMAN_GATE,
            ("terminate_blocked_run", "drain_current_atomic_unit"),
        ),
        _node("terminal", GraphNodeKind.TERMINAL),
    ),
)


GRAPH_DEFINITIONS = {
    SYNC_GRAPH_V1.graph_version: SYNC_GRAPH_V1,
    SYNC_GRAPH_V2.graph_version: SYNC_GRAPH_V2,
    ROLLBACK_GRAPH_V1.graph_version: ROLLBACK_GRAPH_V1,
}


def get_graph_definition(graph_version: str) -> GraphDefinitionV1:
    try:
        return GRAPH_DEFINITIONS[graph_version]
    except KeyError as error:
        raise ValueError(f"unsupported Agent graph version: {graph_version}") from error

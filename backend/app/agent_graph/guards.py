from enum import StrEnum
from uuid import UUID

from app.agent_graph.contracts import AllowedActionV1
from app.agent_graph.definition import get_graph_definition


class GraphGuardRejected(RuntimeError):
    pass


class ReplanDisposition(StrEnum):
    AUTO_ALLOWED = "auto_allowed"
    HUMAN_GATE_REQUIRED = "human_gate_required"
    MODEL_ERROR_BLOCKED = "model_error_blocked"


class GraphGuardService:
    def replan_disposition(
        self,
        *,
        replan_count: int,
        cross_phase: bool,
    ) -> ReplanDisposition:
        if cross_phase:
            return ReplanDisposition.HUMAN_GATE_REQUIRED
        if replan_count >= 3:
            return ReplanDisposition.MODEL_ERROR_BLOCKED
        return ReplanDisposition.AUTO_ALLOWED

    def validate_fencing(
        self,
        *,
        expected_worker_id: str,
        expected_lease_token: UUID,
        expected_attempt_count: int,
        persisted_worker_id: str | None,
        persisted_lease_token: UUID | None,
        persisted_attempt_count: int,
    ) -> None:
        if (
            persisted_worker_id != expected_worker_id
            or persisted_lease_token != expected_lease_token
            or persisted_attempt_count != expected_attempt_count
        ):
            raise GraphGuardRejected("stale_fencing")

    def validate_action_path(
        self,
        *,
        graph_version: str,
        current_node: str,
        action: AllowedActionV1,
    ) -> None:
        if (
            action.kind == "terminate"
            and (action.graph_action_kind or action.action_id)
            == "terminate_requested"
            and action.successor_node == "drain_current_atomic_unit"
            and current_node != "terminal"
        ):
            return
        node = get_graph_definition(graph_version).node(current_node)
        action_kind = action.graph_action_kind or action.action_id
        legal = any(
            template.action_kind == action_kind
            and template.successor_node == action.successor_node
            for template in node.action_templates
        )
        if not legal:
            raise GraphGuardRejected(
                f"action path is outside graph definition: {action_kind}"
            )

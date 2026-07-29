from app.agent_graph.contracts import (
    AllowedActionSetV1,
    SupervisorContextV1,
)
from app.models.agent_graph import AgentGraphRunRecord
from app.models.agent_runtime import AgentRunRecord


def build_supervisor_context(
    state: AgentGraphRunRecord,
    parent_run: AgentRunRecord,
    action_set: AllowedActionSetV1,
) -> SupervisorContextV1:
    active_blockers = tuple(
        sorted(
            {
                f"guard:{code}"
                for summary in action_set.excluded_action_summaries
                for code in summary.rejected_guard_codes
            }
        )
    )
    pending_work_summary = tuple(
        f"{action.sub_agent or 'deterministic'}:{action.action_id}:"
        f"{len(action.resource_ids)}"
        for action in action_set.allowed_actions
    )
    evidence_refs = tuple(
        sorted(
            {
                evidence_ref
                for action in action_set.allowed_actions
                for evidence_ref in action.required_evidence
            }
        )
    )
    human_gate_summary = tuple(
        f"required:{action.action_id}"
        for action in action_set.allowed_actions
        if action.requires_human
    )
    return SupervisorContextV1(
        tenant_ref=f"tenant-ref:{state.id}",
        task_id=str(parent_run.task_id),
        run_id=str(parent_run.id),
        run_kind=parent_run.kind,
        workflow_version="agent-graph-v1",
        graph_version=state.graph_version,
        current_node=state.current_node,
        graph_cursor=state.cursor,
        status=state.status,
        action_set=action_set,
        active_blockers=active_blockers,
        completed_action_summary=(
            (f"completed_action_count:{state.cursor}",)
            if state.cursor
            else ()
        ),
        pending_work_summary=pending_work_summary,
        evidence_manifest_refs=evidence_refs,
        human_gate_summary=human_gate_summary,
        retry_and_replan_budget=max(0, 3 - state.replan_count),
        termination_requested=state.termination_requested,
    )

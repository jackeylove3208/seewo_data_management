from collections.abc import Sequence
from uuid import UUID

from app.agent_graph.contracts import (
    AllowedActionSetV1,
    CandidateActionEvaluationV1,
    SupervisorContextV1,
)
from app.agent_graph.repository import AgentGraphNotFound, AgentGraphRepository
from app.ai.graph_supervisor import GraphSupervisorAgent
from app.models.agent_graph import AgentGraphRunRecord, AgentSupervisorDecisionRecord
from app.models.agent_runtime import AgentRunRecord


class SupervisorDecisionService:
    def __init__(
        self,
        *,
        repository: AgentGraphRepository,
        agent: GraphSupervisorAgent,
    ) -> None:
        self._repository = repository
        self._agent = agent

    async def decide_and_record(
        self,
        *,
        graph_run_id: UUID,
        state: AgentGraphRunRecord,
        candidate_evaluations: Sequence[CandidateActionEvaluationV1],
        action_set: AllowedActionSetV1,
    ) -> AgentSupervisorDecisionRecord:
        if state.id != graph_run_id:
            raise AgentGraphNotFound("Agent graph state does not match graph_run_id")
        parent_run = await self._repository.session.get(AgentRunRecord, state.run_id)
        if parent_run is None or parent_run.workflow_version != "agent-graph-v1":
            raise AgentGraphNotFound("agent-graph-v1 parent run not found")
        candidate_record = await self._repository.record_candidate_set(
            graph_run_id=state.id,
            cursor=state.cursor,
            candidate_evaluations=candidate_evaluations,
            action_set=action_set,
        )
        context = SupervisorContextV1(
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
            retry_and_replan_budget=max(0, 3 - state.replan_count),
            termination_requested=state.termination_requested,
        )
        result = await self._agent.decide_with_provenance(context)
        return await self._repository.record_decision(
            candidate_set_id=candidate_record.id,
            decision=result.decision,
            model_provenance={
                "provider": result.provider,
                "model": result.model,
                "request_id": result.request_id,
                "attempt_count": result.attempt_count,
            },
        )

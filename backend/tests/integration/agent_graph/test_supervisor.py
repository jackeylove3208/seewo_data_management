from uuid import uuid4

import pytest

from app.agent_graph.actions import build_allowed_action_set
from app.agent_graph.contracts import AllowedActionV1, CandidateActionEvaluationV1
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.supervisor import SupervisorDecisionService
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.ai.graph_supervisor import GraphSupervisorAgent
from app.ai.providers.base import LLMRequest, LLMResponse
from app.models.reconciliation import ReconciliationTask


class Provider:
    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        del request
        return LLMResponse(
            output={
                "result": {
                    "action_id": "inspect_target",
                    "reason_zh": "目标来源需要先完成检查。",
                    "expected_result": "target-inspection-v1",
                    "observed_blockers": [],
                    "risk_notes_zh": [],
                    "why_not_other_actions_zh": [
                        {
                            "action_id": "inspect_authority",
                            "reason_zh": "权威来源可在下一动作检查。",
                        }
                    ],
                    "operator_message_zh": "正在检查目标来源。",
                }
            },
            provider="scripted",
            model="test-supervisor",
            request_id="supervisor-request-1",
        )


def _candidate(
    action_id: str,
    evidence: str,
) -> CandidateActionEvaluationV1:
    return CandidateActionEvaluationV1(
        passed=True,
        action=AllowedActionV1(
            action_id=action_id,
            kind="dispatch_sub_agent",
            sub_agent="source-inspection",
            resource_ids=(f"resource:{action_id}",),
            required_evidence=(evidence,),
            risk="low",
            requires_human=False,
            successor_node="inspect_sources",
        ),
    )


@pytest.mark.asyncio
async def test_decision_service_persists_candidate_set_decision_and_provenance(
    session,
) -> None:
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="created",
        stage="ingestion",
        workflow_version="agent-graph-v1",
        idempotency_key=str(uuid4()),
        request_hash="request-hash",
    )
    session.add(task)
    await session.flush()
    run = await AgentRuntimeRepository(session).create_run(
        task_id=task.id,
        tenant_id=task.tenant_id,
        conversation_id=None,
        kind=AgentRunKind.SYNC,
        workflow_version="agent-graph-v1",
    )
    repository = AgentGraphRepository(session)
    state = await repository.create_run_state(
        run_id=run.id,
        graph_version="agent-sync-graph-v1",
        initial_node="inspect_sources",
    )
    candidates = (
        _candidate("inspect_authority", "authority-inspection-v1"),
        _candidate("inspect_target", "target-inspection-v1"),
    )
    action_set = build_allowed_action_set(candidates)

    record = await SupervisorDecisionService(
        repository=repository,
        agent=GraphSupervisorAgent(Provider()),
    ).decide_and_record(
        graph_run_id=state.id,
        state=state,
        candidate_evaluations=candidates,
        action_set=action_set,
    )

    assert record.selected_action_id == "inspect_target"
    assert record.model_provenance == {
        "provider": "scripted",
        "model": "test-supervisor",
        "request_id": "supervisor-request-1",
        "attempt_count": 1,
    }


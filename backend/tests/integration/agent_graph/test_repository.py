from uuid import uuid4

import pytest

from app.agent_graph.actions import build_allowed_action_set
from app.agent_graph.contracts import (
    AllowedActionV1,
    CandidateActionEvaluationV1,
    SupervisorDecisionV1,
    UnselectedActionReasonV1,
)
from app.agent_graph.repository import (
    AgentGraphRepository,
    GraphCursorConflict,
    GraphFactConflict,
)
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.models.reconciliation import ReconciliationTask


async def _graph_run(session):
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
    return task, run


def _candidate(action_id: str, successor: str) -> CandidateActionEvaluationV1:
    return CandidateActionEvaluationV1(
        passed=True,
        action=AllowedActionV1(
            action_id=action_id,
            kind="dispatch_sub_agent",
            sub_agent="source-inspection",
            resource_ids=(f"resource:{action_id}",),
            required_evidence=(f"{action_id}-evidence-v1",),
            risk="low",
            requires_human=False,
            successor_node=successor,
        ),
    )


@pytest.mark.asyncio
async def test_transition_uses_compare_and_swap_cursor_and_is_append_only(session) -> None:
    _task, run = await _graph_run(session)
    repository = AgentGraphRepository(session)
    state = await repository.create_run_state(
        run_id=run.id,
        graph_version="agent-sync-graph-v1",
        initial_node="intent_confirmed",
    )

    first = await repository.record_transition(
        state.id,
        expected_cursor=0,
        from_node="intent_confirmed",
        to_node="acquire_school_lock",
        action_id="acquire_school_lock",
        guard_results={"workflow_version": "passed"},
        fencing_token=1,
    )

    assert first.cursor == 1
    assert state.cursor == 1
    assert state.current_node == "acquire_school_lock"
    with pytest.raises(GraphCursorConflict):
        await repository.record_transition(
            state.id,
            expected_cursor=0,
            from_node="intent_confirmed",
            to_node="inspect_sources",
            action_id="skip_lock",
            guard_results={"workflow_version": "passed"},
            fencing_token=1,
        )
    assert [item.id for item in await repository.list_transitions(state.id)] == [
        first.id
    ]


@pytest.mark.asyncio
async def test_candidate_set_and_supervisor_decision_preserve_complete_audit(session) -> None:
    _task, run = await _graph_run(session)
    repository = AgentGraphRepository(session)
    state = await repository.create_run_state(
        run_id=run.id,
        graph_version="agent-sync-graph-v1",
        initial_node="inspect_sources",
    )
    evaluations = (
        _candidate("inspect_authority", "inspect_sources"),
        _candidate("inspect_target", "inspect_sources"),
    )
    action_set = build_allowed_action_set(evaluations)
    candidate_record = await repository.record_candidate_set(
        graph_run_id=state.id,
        cursor=0,
        candidate_evaluations=evaluations,
        action_set=action_set,
    )
    decision = SupervisorDecisionV1(
        action_id="inspect_authority",
        reason_zh="先检查权威数据。",
        expected_result="inspect_authority-evidence-v1",
        why_not_other_actions_zh=(
            UnselectedActionReasonV1(
                action_id="inspect_target",
                reason_zh="目标数据可以在下一动作检查。",
            ),
        ),
    )
    decision_record = await repository.record_decision(
        candidate_set_id=candidate_record.id,
        decision=decision,
        model_provenance={
            "provider": "scripted",
            "model": "test",
            "request_id": "request-1",
        },
    )

    assert candidate_record.action_set_hash == action_set.action_set_hash
    assert len(candidate_record.candidate_evaluations) == 2
    assert decision_record.selected_action_id == "inspect_authority"
    assert decision_record.decision["why_not_other_actions_zh"][0]["action_id"] == (
        "inspect_target"
    )
    with pytest.raises(GraphFactConflict, match="decision"):
        await repository.record_decision(
            candidate_set_id=candidate_record.id,
            decision=decision,
            model_provenance={"provider": "duplicate"},
        )


@pytest.mark.asyncio
async def test_manifest_invocation_tool_and_human_gate_are_linked_facts(session) -> None:
    _task, run = await _graph_run(session)
    repository = AgentGraphRepository(session)
    state = await repository.create_run_state(
        run_id=run.id,
        graph_version="agent-sync-graph-v1",
        initial_node="analyze_actionable_batches",
    )
    manifest = await repository.record_manifest(
        graph_run_id=state.id,
        cursor=0,
        graph_node="analyze_actionable_batches",
        action_id="analyze_students",
        manifest={
            "resource_ids": ["work-item:1"],
            "allowed_evidence_refs": ["paired-record:1"],
        },
        content_hash="sha256:" + ("1" * 64),
    )
    invocation = await repository.record_invocation(
        graph_run_id=state.id,
        cursor=0,
        action_id="analyze_students",
        evidence_manifest_id=manifest.id,
        execution_mode="skill_model",
        skill_name="reconcile-entity-batch",
        skill_version="1.0.0",
        schema_version="agent-finding-v1",
        attempt=1,
        status="completed",
        input_hash="sha256:" + ("2" * 64),
        output_hash="sha256:" + ("3" * 64),
        model_provenance={"request_id": "request-2"},
    )
    tool_call = await repository.record_tool_call(
        invocation_id=invocation.id,
        tool_name="read_paired_record_evidence",
        arguments_hash="sha256:" + ("4" * 64),
        result_hash="sha256:" + ("5" * 64),
        authorized=True,
        status="completed",
        trace_id="trace-1",
    )
    gate = await repository.record_human_gate(
        graph_run_id=state.id,
        cursor=0,
        gate_kind="high_risk_approval",
        member_ids=("finding:1",),
        content_hash="sha256:" + ("6" * 64),
        status="pending",
    )

    assert invocation.evidence_manifest_id == manifest.id
    assert tool_call.invocation_id == invocation.id
    assert tool_call.model_turn is None
    assert tool_call.replay_descriptor is None
    assert gate.member_ids == ["finding:1"]


@pytest.mark.asyncio
async def test_replayable_tool_calls_are_ordered_across_semantic_attempts(session) -> None:
    _task, run = await _graph_run(session)
    repository = AgentGraphRepository(session)
    state = await repository.create_run_state(
        run_id=run.id,
        graph_version="agent-sync-graph-v1",
        initial_node="analyze_actionable_batches",
    )
    manifest = await repository.record_manifest(
        graph_run_id=state.id,
        cursor=0,
        graph_node="analyze_actionable_batches",
        action_id="analyze_batch_1",
        manifest={
            "resource_ids": ["work-item:1"],
            "allowed_evidence_refs": ["paired-record:1"],
        },
        content_hash="sha256:" + ("7" * 64),
    )
    invocations = []
    for attempt in (1, 2):
        invocations.append(
            await repository.record_invocation(
                graph_run_id=state.id,
                cursor=0,
                action_id="analyze_batch_1",
                evidence_manifest_id=manifest.id,
                execution_mode="skill_model",
                skill_name="reconcile-entity-batch",
                skill_version="1.0.0",
                schema_version="agent-finding-v1",
                attempt=attempt,
                status="completed",
                input_hash="sha256:input",
                output_hash=f"sha256:output-{attempt}",
                model_provenance={},
            )
        )
    await repository.record_tool_call(
        invocation_id=invocations[0].id,
        tool_name="read_work_item",
        arguments_hash="sha256:arguments-1",
        result_hash="sha256:result-1",
        authorized=True,
        status="completed",
        trace_id="trace-replay-1",
        model_turn=1,
        replay_descriptor={"resource_id": "work-item:1"},
    )
    await repository.record_tool_call(
        invocation_id=invocations[1].id,
        tool_name="read_claim_state",
        arguments_hash="sha256:arguments-2",
        result_hash="sha256:result-2",
        authorized=True,
        status="completed",
        trace_id="trace-replay-2",
        model_turn=2,
        replay_descriptor={"resource_id": "work-item:1"},
    )
    await repository.record_tool_call(
        invocation_id=invocations[1].id,
        tool_name="read_work_item",
        arguments_hash="sha256:ignored",
        result_hash="sha256:ignored",
        authorized=False,
        status="denied",
        trace_id="trace-denied",
        model_turn=3,
        replay_descriptor=None,
    )

    calls = await repository.list_replayable_tool_calls(
        graph_run_id=state.id,
        cursor=state.cursor,
        action_id="analyze_batch_1",
        skill_name="reconcile-entity-batch",
        input_hash="sha256:input",
    )

    assert [(item.model_turn, item.tool_name) for item in calls] == [
        (1, "read_work_item"),
        (2, "read_claim_state"),
    ]
    assert calls[0].replay_descriptor == {"resource_id": "work-item:1"}

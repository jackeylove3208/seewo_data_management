import asyncio

import pytest
from sqlalchemy import select

from app.agent_graph.contracts import (
    AllowedActionV1,
    CandidateActionEvaluationV1,
    SupervisorDecisionV1,
    UnselectedActionReasonV1,
)
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.worker import (
    AgentGraphLeaseLost,
    AgentGraphWorker,
    GraphActionOutcome,
    GraphCandidatePlan,
    GraphWorkContext,
)
from app.agent_runtime.service import AgentSupervisorService
from app.core.security import OperatorContext
from app.models.agent_graph import AgentSupervisorDecisionRecord
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask


class Supervisor:
    def __init__(self, choice: str) -> None:
        self.choice = choice

    async def decide_with_provenance(self, context):
        from app.ai.graph_supervisor import GraphSupervisorCallResult

        selected = next(
            action for action in context.allowed_actions if action.action_id == self.choice
        )
        reasons = tuple(
            UnselectedActionReasonV1(
                action_id=action.action_id,
                reason_zh="本轮选择另一条合法路径。",
            )
            for action in context.allowed_actions
            if action.action_id != self.choice
        )
        return GraphSupervisorCallResult(
            decision=SupervisorDecisionV1(
                action_id=selected.action_id,
                reason_zh="根据当前证据选择该动作。",
                expected_result=selected.required_evidence[0],
                why_not_other_actions_zh=reasons,
            ),
            provider="scripted",
            model="test",
            request_id=f"request:{self.choice}",
            attempt_count=1,
        )


def _candidate(
    action_id: str,
    *,
    graph_action_kind: str,
    resource_id: str,
    evidence: str,
    successor: str,
) -> CandidateActionEvaluationV1:
    return CandidateActionEvaluationV1(
        passed=True,
        action=AllowedActionV1(
            action_id=action_id,
            graph_action_kind=graph_action_kind,
            kind="dispatch_sub_agent",
            sub_agent="source-inspection",
            resource_ids=(resource_id,),
            required_evidence=(evidence,),
            risk="low",
            requires_human=False,
            successor_node=successor,
        ),
    )


async def _start_graph_run(database) -> tuple:
    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-graph-worker",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="agent-graph-v1",
            idempotency_key="graph-worker-task",
            request_hash="graph-worker-task",
        )
        session.add(task)
        await session.flush()
        run = await AgentSupervisorService(
            session,
            operator=OperatorContext(
                operator_id="operator-1",
                tenant_id="school-graph-worker",
            ),
        ).start(task_id=task.id, conversation_id=None)
        await session.commit()
        return task.id, run.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("choice", "expected_resource", "expected_successor"),
    [
        ("inspect_authority:page-1", "authority:page-1", "inspect_sources"),
        (
            "normalize_ready_sources:pair-1",
            "snapshot-pair:1",
            "normalize_input_batches",
        ),
    ],
)
async def test_supervisor_choices_dispatch_different_work_and_paths(
    database,
    choice: str,
    expected_resource: str,
    expected_successor: str,
) -> None:
    _task_id, run_id = await _start_graph_run(database)
    candidates = (
        _candidate(
            "inspect_authority:page-1",
            graph_action_kind="inspect_authority",
            resource_id="authority:page-1",
            evidence="authority-inspection-v1",
            successor="inspect_sources",
        ),
        _candidate(
            "normalize_ready_sources:pair-1",
            graph_action_kind="normalize_ready_sources",
            resource_id="snapshot-pair:1",
            evidence="normalization-work-v1",
            successor="normalize_input_batches",
        ),
    )

    async def plan(_context: GraphWorkContext) -> GraphCandidatePlan:
        return GraphCandidatePlan(candidate_evaluations=candidates)

    dispatched: list[tuple[str, tuple[str, ...]]] = []

    async def execute(
        _context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        dispatched.append((action.action_id, action.resource_ids))
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=(action.required_evidence[0],),
        )

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id=f"worker:{choice}",
        lease_seconds=60,
        supervisor=Supervisor(choice),
        candidate_provider=plan,
        executor=execute,
    )

    assert await worker.run_once() is True
    assert dispatched == [(choice, (expected_resource,))]
    async with database.session_factory() as session:
        run_state = await AgentGraphRepository(session).get_run_state_for_agent_run(
            run_id
        )
        assert run_state is not None
        assert run_state.current_node == expected_successor
        decision = await session.scalar(
            select(AgentSupervisorDecisionRecord).where(
                AgentSupervisorDecisionRecord.graph_run_id == run_state.id
            )
        )
        assert decision is not None
        assert decision.selected_action_id == choice


@pytest.mark.asyncio
async def test_old_worker_claim_filter_does_not_claim_graph_runs(database) -> None:
    _task_id, _run_id = await _start_graph_run(database)
    called = False

    async def old_handler(_context):
        nonlocal called
        called = True
        raise AssertionError("legacy worker claimed an agent-graph-v1 run")

    from app.agent_runtime.state_machine import AgentPhase
    from app.agent_runtime.worker import AgentWorker

    worker = AgentWorker(
        database.session_factory,
        worker_id="legacy-worker",
        lease_seconds=60,
        handlers={AgentPhase.INGEST_AND_NORMALIZE: old_handler},
    )

    assert await worker.run_once() is False
    assert called is False


@pytest.mark.asyncio
async def test_graph_worker_cancels_executor_when_lease_is_lost(database) -> None:
    _task_id, run_id = await _start_graph_run(database)
    candidate = _candidate(
        "inspect_authority:page-1",
        graph_action_kind="inspect_authority",
        resource_id="authority:page-1",
        evidence="authority-inspection-v1",
        successor="inspect_sources",
    )

    async def plan(_context: GraphWorkContext) -> GraphCandidatePlan:
        from app.agent_graph.contracts import SingleActionReasonCode

        return GraphCandidatePlan(
            candidate_evaluations=(candidate,),
            single_action_reason_code=SingleActionReasonCode.ONLY_GUARD_SATISFIED,
        )

    executor_started = asyncio.Event()
    executor_cancelled = asyncio.Event()

    async def execute(
        _context: GraphWorkContext,
        _action: AllowedActionV1,
    ) -> GraphActionOutcome:
        executor_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            executor_cancelled.set()

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="graph-worker-fencing",
        lease_seconds=1,
        heartbeat_interval_seconds=0.01,
        supervisor=Supervisor("inspect_authority:page-1"),
        candidate_provider=plan,
        executor=execute,
    )
    worker_task = asyncio.create_task(worker.run_once())
    await executor_started.wait()
    async with database.session_factory() as session:
        async with session.begin():
            run = await session.get(AgentRunRecord, run_id)
            assert run is not None
            run.lease_owner = "replacement-worker"

    with pytest.raises(AgentGraphLeaseLost):
        await asyncio.wait_for(worker_task, timeout=1)
    assert executor_cancelled.is_set()

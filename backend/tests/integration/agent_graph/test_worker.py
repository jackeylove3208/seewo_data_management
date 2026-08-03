import asyncio

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.agent_graph.actions import build_allowed_action_set
from app.agent_graph.contracts import (
    AllowedActionV1,
    CandidateActionEvaluationV1,
    SingleActionReasonCode,
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
from app.agent_runtime.errors import ExternalWriteRecoveryRequired
from app.agent_runtime.service import AgentSupervisorService
from app.api_connectors.secrets import (
    EncryptedDatabaseSecretStore,
    SecretReferenceError,
)
from app.core.security import OperatorContext
from app.models.agent_graph import AgentSupervisorDecisionRecord
from app.models.agent_runtime import (
    AgentConversationRecord,
    AgentFailureRecord,
    AgentRunRecord,
    AgentTaskEventRecord,
    SchoolTaskLockRecord,
)
from app.models.api_connectors import ApiConnectionRecord
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
async def test_single_guarded_action_replays_without_supervisor_or_replanning(
    database,
) -> None:
    _task_id, _run_id = await _start_graph_run(database)
    candidate = _candidate(
        "inspect_authority:page-1",
        graph_action_kind="inspect_authority",
        resource_id="authority:page-1",
        evidence="authority-inspection-v1",
        successor="inspect_sources",
    )
    plan_calls = 0

    async def plan(_context: GraphWorkContext) -> GraphCandidatePlan:
        nonlocal plan_calls
        plan_calls += 1
        if plan_calls > 1:
            raise AssertionError("recovery recalculated a frozen candidate set")
        from app.agent_graph.contracts import SingleActionReasonCode

        return GraphCandidatePlan(
            candidate_evaluations=(candidate,),
            single_action_reason_code=SingleActionReasonCode.ONLY_GUARD_SATISFIED,
        )

    class SupervisorMustNotRun:
        async def decide_with_provenance(self, _context):
            raise AssertionError("a single guarded action called the model Supervisor")

    execution_calls = 0

    async def execute(
        _context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        nonlocal execution_calls
        execution_calls += 1
        if execution_calls == 1:
            raise RuntimeError("simulated crash after decision persistence")
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=action.required_evidence,
        )

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="graph-worker-recovery",
        lease_seconds=60,
        supervisor=SupervisorMustNotRun(),
        candidate_provider=plan,
        executor=execute,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        await worker.run_once()
    assert await worker.run_once() is True
    assert plan_calls == 1
    assert execution_calls == 2


@pytest.mark.asyncio
async def test_non_retryable_database_error_fails_run_and_releases_lock(
    database,
) -> None:
    task_id, run_id = await _start_graph_run(database)
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

    class PostgreSQLDataError(Exception):
        sqlstate = "22001"

    execution_calls = 0

    async def execute(
        _context: GraphWorkContext,
        _action: AllowedActionV1,
    ) -> GraphActionOutcome:
        nonlocal execution_calls
        execution_calls += 1
        raise DBAPIError(
            "INSERT INTO agent_checkpoints (...) VALUES (...)",
            {},
            PostgreSQLDataError("value too long"),
            connection_invalidated=False,
        )

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="graph-worker-database-contract-error",
        lease_seconds=60,
        supervisor=Supervisor("inspect_authority:page-1"),
        candidate_provider=plan,
        executor=execute,
    )

    assert await worker.run_once() is True
    assert await worker.run_once() is False
    assert execution_calls == 1
    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        task = await session.get(ReconciliationTask, task_id)
        state = await AgentGraphRepository(session).get_run_state_for_agent_run(
            run_id
        )
        active_lock = await session.scalar(
            select(SchoolTaskLockRecord.id).where(
                SchoolTaskLockRecord.owner_run_id == run_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )
        failure = await session.scalar(
            select(AgentFailureRecord).where(AgentFailureRecord.run_id == run_id)
        )
        event = await session.scalar(
            select(AgentTaskEventRecord)
            .where(
                AgentTaskEventRecord.run_id == run_id,
                AgentTaskEventRecord.event_type == "run.failed",
            )
            .order_by(AgentTaskEventRecord.sequence.desc())
        )

    assert run is not None
    assert task is not None
    assert state is not None
    assert failure is not None
    assert event is not None
    assert run.status == "failed"
    assert state.status == "failed"
    assert task.status == "failed"
    assert active_lock is None
    assert failure.code == "agent_persistence_contract_error"
    assert event.payload["failed_node"] == "inspect_sources"
    assert "value too long" not in str(event.payload)


@pytest.mark.asyncio
async def test_non_retryable_action_error_fails_once_instead_of_retrying_forever(
    database,
) -> None:
    task_id, run_id = await _start_graph_run(database)
    candidate = _candidate(
        "inspect_authority:page-1",
        graph_action_kind="inspect_authority",
        resource_id="authority:page-1",
        evidence="authority-inspection-v1",
        successor="inspect_sources",
    )

    async def plan(_context: GraphWorkContext) -> GraphCandidatePlan:
        return GraphCandidatePlan(
            candidate_evaluations=(candidate,),
            single_action_reason_code=SingleActionReasonCode.ONLY_GUARD_SATISFIED,
        )

    execution_calls = 0

    async def execute(
        _context: GraphWorkContext,
        _action: AllowedActionV1,
    ) -> GraphActionOutcome:
        nonlocal execution_calls
        execution_calls += 1
        raise ValueError("unsafe raw target detail must not reach the UI")

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="graph-worker-action-contract-error",
        lease_seconds=60,
        supervisor=Supervisor("inspect_authority:page-1"),
        candidate_provider=plan,
        executor=execute,
    )

    assert await worker.run_once() is True
    assert await worker.run_once() is False
    assert execution_calls == 1
    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        task = await session.get(ReconciliationTask, task_id)
        state = await AgentGraphRepository(session).get_run_state_for_agent_run(
            run_id
        )
        failure = await session.scalar(
            select(AgentFailureRecord).where(
                AgentFailureRecord.run_id == run_id
            )
        )
        active_lock = await session.scalar(
            select(SchoolTaskLockRecord.id).where(
                SchoolTaskLockRecord.owner_run_id == run_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )

    assert run is not None and run.status == "failed"
    assert task is not None and task.status == "failed"
    assert state is not None and state.status == "failed"
    assert failure is not None
    assert failure.code == "agent_action_contract_error"
    assert "raw target detail" not in failure.safe_message
    assert active_lock is None


@pytest.mark.asyncio
async def test_ambiguous_external_write_error_remains_replayable(
    database,
) -> None:
    task_id, run_id = await _start_graph_run(database)
    async with database.session_factory() as session:
        async with session.begin():
            run = await session.get(AgentRunRecord, run_id)
            task = await session.get(ReconciliationTask, task_id)
            state = await AgentGraphRepository(
                session
            ).get_run_state_for_agent_run(run_id, for_update=True)
            assert run is not None and task is not None and state is not None
            run.kind = "rollback"
            run.phase = "execute_restore"
            task.task_kind = "rollback"
            task.status = "running"
            state.graph_version = "agent-rollback-graph-v1"
            state.current_node = "execute_restore_operations"

    candidate = _candidate(
        "verify_restore_operations",
        graph_action_kind="verify_restore_operations",
        resource_id="rollback-operation:1",
        evidence="rollback-outcomes:v1",
        successor="verify_restore_operations",
    )

    async def plan(_context: GraphWorkContext) -> GraphCandidatePlan:
        return GraphCandidatePlan(
            candidate_evaluations=(candidate,),
            single_action_reason_code=(
                SingleActionReasonCode.ONLY_GUARD_SATISFIED
            ),
        )

    async def execute(
        _context: GraphWorkContext,
        _action: AllowedActionV1,
    ) -> GraphActionOutcome:
        raise ExternalWriteRecoveryRequired(
            "external commit needs recovery"
        )

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="graph-worker-replay-external-write",
        lease_seconds=60,
        supervisor=Supervisor("verify_restore_operations"),
        candidate_provider=plan,
        executor=execute,
    )

    with pytest.raises(
        ExternalWriteRecoveryRequired,
        match="external commit needs recovery",
    ):
        await worker.run_once()

    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        task = await session.get(ReconciliationTask, task_id)
        state = await AgentGraphRepository(
            session
        ).get_run_state_for_agent_run(run_id)
        failure = await session.scalar(
            select(AgentFailureRecord).where(
                AgentFailureRecord.run_id == run_id
            )
        )
        active_lock = await session.scalar(
            select(SchoolTaskLockRecord.id).where(
                SchoolTaskLockRecord.owner_run_id == run_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )

    assert run is not None and run.status == "running"
    assert task is not None and task.status == "running"
    assert state is not None
    assert state.current_node == "execute_restore_operations"
    assert failure is None
    assert active_lock is not None


@pytest.mark.asyncio
async def test_termination_request_supersedes_a_frozen_candidate_set(
    database,
) -> None:
    _task_id, run_id = await _start_graph_run(database)
    stale_candidate = _candidate(
        "inspect_sources",
        graph_action_kind="inspect_sources",
        resource_id="source-pair",
        evidence="sources:ready",
        successor="inspect_sources",
    )
    async with database.session_factory() as session:
        async with session.begin():
            repository = AgentGraphRepository(session)
            state = await repository.get_run_state_for_agent_run(
                run_id,
                for_update=True,
            )
            assert state is not None
            await repository.record_candidate_set(
                graph_run_id=state.id,
                cursor=state.cursor,
                candidate_evaluations=(stale_candidate,),
                action_set=build_allowed_action_set(
                    (stale_candidate,),
                    single_action_reason_code=(
                        SingleActionReasonCode.SAFETY_MANDATORY
                    ),
                ),
            )
            state.termination_requested = True

    termination = CandidateActionEvaluationV1(
        passed=True,
        action=AllowedActionV1(
            action_id="terminate_requested",
            graph_action_kind="terminate_requested",
            kind="terminate",
            required_evidence=("termination-request:accepted",),
            risk="low",
            requires_human=False,
            successor_node="drain_current_atomic_unit",
        ),
    )
    plan_calls = 0

    async def plan(_context: GraphWorkContext) -> GraphCandidatePlan:
        nonlocal plan_calls
        plan_calls += 1
        return GraphCandidatePlan(
            candidate_evaluations=(termination,),
            single_action_reason_code=(
                SingleActionReasonCode.TERMINATION_REQUESTED
            ),
        )

    executed_actions: list[str] = []

    async def execute(
        _context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        executed_actions.append(action.action_id)
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=action.required_evidence,
        )

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="graph-worker-termination-priority",
        lease_seconds=60,
        supervisor=Supervisor("terminate_requested"),
        candidate_provider=plan,
        executor=execute,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as session:
        state = await AgentGraphRepository(session).get_run_state_for_agent_run(
            run_id
        )

    assert plan_calls == 1
    assert executed_actions == ["terminate_requested"]
    assert state is not None
    assert state.current_node == "drain_current_atomic_unit"


@pytest.mark.asyncio
async def test_deterministic_graph_node_does_not_call_supervisor_and_releases_terminal_lock(
    database,
) -> None:
    _task_id, run_id = await _start_graph_run(database)
    async with database.session_factory() as session:
        async with session.begin():
            state = await AgentGraphRepository(session).get_run_state_for_agent_run(
                run_id,
                for_update=True,
            )
            run = await session.get(AgentRunRecord, run_id)
            assert state is not None
            assert run is not None
            state.current_node = "generate_terminal_report"
            state.cursor = 20
            run.phase = "generate_report"

    candidate = CandidateActionEvaluationV1(
        passed=True,
        action=AllowedActionV1(
            action_id="finish_terminal_report",
            graph_action_kind="finish_terminal_report",
            kind="run_deterministic",
            resource_ids=("report:final",),
            required_evidence=("report:ready",),
            risk="low",
            requires_human=False,
            successor_node="terminal",
        ),
    )

    async def plan(_context: GraphWorkContext) -> GraphCandidatePlan:
        from app.agent_graph.contracts import SingleActionReasonCode

        return GraphCandidatePlan(
            candidate_evaluations=(candidate,),
            single_action_reason_code=SingleActionReasonCode.TERMINALIZATION_REQUIRED,
        )

    class SupervisorMustNotRun:
        async def decide_with_provenance(self, _context):
            raise AssertionError("deterministic graph node called the model Supervisor")

    async def execute(
        _context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=("report:ready",),
        )

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="graph-worker-terminal",
        lease_seconds=60,
        supervisor=SupervisorMustNotRun(),
        candidate_provider=plan,
        executor=execute,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        state = await AgentGraphRepository(session).get_run_state_for_agent_run(run_id)
        active_lock = await session.scalar(
            select(SchoolTaskLockRecord.id).where(
                SchoolTaskLockRecord.owner_run_id == run_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )
        decision = await session.scalar(
            select(AgentSupervisorDecisionRecord).where(
                AgentSupervisorDecisionRecord.graph_run_id == state.id
            )
        )
        assert run is not None
        assert state is not None
        assert run.phase == "terminal"
        assert run.status == "completed"
        assert state.current_node == "terminal"
        assert state.status == "completed"
        assert active_lock is None
        assert decision is not None
        assert decision.model_provenance["mode"] == "deterministic_guarded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_categories", "attempt_count", "expected_code"),
    [
        (("tool_authorization_failure",), 1, "agent_tool_authorization_failed"),
        (("evidence_manifest_missing",), 0, "agent_evidence_contract_failed"),
    ],
)
async def test_model_failure_blocks_graph_run_and_keeps_school_lock(
    database,
    failure_categories,
    attempt_count,
    expected_code,
) -> None:
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

    async def execute(_context, _action):
        from app.ai.graph_subagents import GraphSubAgentFailure

        error = GraphSubAgentFailure(
            "provider detail must not escape",
            failure_categories=failure_categories,
            attempt_count=attempt_count,
        )
        error.attempt_details = (
            {
                "attempt": max(attempt_count, 1),
                "safe_error_code": failure_categories[0],
                "status_class": "transport",
                "duration_ms": 1_500,
                "request_id": "gateway-request-safe",
                "transport_attempts": 3,
                "repair_feedback": [
                    {
                        "path": "result.findings",
                        "code": "missing_required_field",
                    }
                ],
            },
        )
        raise error

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id=f"graph-worker-model-error-{expected_code}",
        lease_seconds=60,
        supervisor=Supervisor("inspect_authority:page-1"),
        candidate_provider=plan,
        executor=execute,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        state = await AgentGraphRepository(session).get_run_state_for_agent_run(run_id)
        active_lock = await session.scalar(
            select(SchoolTaskLockRecord.id).where(
                SchoolTaskLockRecord.owner_run_id == run_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )
        event = await session.scalar(
            select(AgentTaskEventRecord).where(
                AgentTaskEventRecord.run_id == run_id,
                AgentTaskEventRecord.event_type == "run.blocked_model_error",
            )
        )
        failure = await session.scalar(
            select(AgentFailureRecord).where(AgentFailureRecord.run_id == run_id)
        )
        assert run is not None
        assert state is not None
        assert run.status == "blocked_model_error"
        assert state.current_node == "blocked_model_error"
        assert state.status == "blocked_model_error"
        assert active_lock is not None
        assert event is not None
        assert event.payload["failed_node"] == "inspect_sources"
        assert event.payload["attempt_count"] == attempt_count
        assert event.payload["failure_categories"] == list(failure_categories)
        assert event.payload["safe_failure_category"] == failure_categories[0]
        assert event.payload["code"] == expected_code
        assert failure is not None
        assert failure.attempt_count == attempt_count
        assert failure.code == expected_code
        assert failure.gateway_request_id == "gateway-request-safe"
        assert failure.details == {
            "attempts": [
                {
                    "attempt": max(attempt_count, 1),
                    "safe_error_code": failure_categories[0],
                    "status_class": "transport",
                    "duration_ms": 1_500,
                    "request_id": "gateway-request-safe",
                    "transport_attempts": 3,
                    "repair_feedback": [
                        {
                            "path": "result.findings",
                            "code": "missing_required_field",
                        }
                    ],
                }
            ],
            "failed_node": "inspect_sources",
            "failure_categories": list(failure_categories),
        }


@pytest.mark.asyncio
async def test_supervisor_exhaustion_records_original_node_and_safe_categories(
    database,
) -> None:
    from app.ai.graph_supervisor import GraphSupervisorFailure

    class FailingSupervisor:
        async def decide_with_provenance(self, _context):
            raise GraphSupervisorFailure(
                "invalid Supervisor decision after 4 attempts",
                failure_categories=(
                    "model_contract_failure",
                    "model_contract_failure",
                    "model_contract_failure",
                    "model_contract_failure",
                ),
            )

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
            "inspect_target:page-1",
            graph_action_kind="inspect_target",
            resource_id="target:page-1",
            evidence="target-inspection-v1",
            successor="inspect_sources",
        ),
    )

    async def plan(_context: GraphWorkContext) -> GraphCandidatePlan:
        return GraphCandidatePlan(candidate_evaluations=candidates)

    async def execute(_context, _action):
        raise AssertionError("executor must not run after Supervisor failure")

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="graph-worker-supervisor-error",
        lease_seconds=60,
        supervisor=FailingSupervisor(),
        candidate_provider=plan,
        executor=execute,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as session:
        event = await session.scalar(
            select(AgentTaskEventRecord)
            .where(
                AgentTaskEventRecord.run_id == run_id,
                AgentTaskEventRecord.event_type == "run.blocked_model_error",
            )
            .order_by(AgentTaskEventRecord.sequence.desc())
        )
        assert event is not None
        assert event.payload["failed_node"] == "inspect_sources"
        assert event.payload["failure_categories"] == [
            "model_contract_failure",
            "model_contract_failure",
            "model_contract_failure",
            "model_contract_failure",
        ]
        assert "invalid Supervisor decision" not in str(event.payload)


@pytest.mark.asyncio
async def test_low_risk_repair_is_bounded_to_three_graph_entries(database) -> None:
    _task_id, run_id = await _start_graph_run(database)
    async with database.session_factory() as session:
        async with session.begin():
            state = await AgentGraphRepository(session).get_run_state_for_agent_run(
                run_id,
                for_update=True,
            )
            run = await session.get(AgentRunRecord, run_id)
            assert state is not None
            assert run is not None
            state.current_node = "analyze_actionable_batches"
            run.phase = "analyze_batches"

    async def plan(context: GraphWorkContext) -> GraphCandidatePlan:
        successor = (
            "repair_analysis_batch"
            if context.current_node == "analyze_actionable_batches"
            else "analyze_actionable_batches"
        )
        candidate = _candidate(
            "repair_analysis_batch",
            graph_action_kind="repair_analysis_batch",
            resource_id="analysis-batch:repairable",
            evidence="analysis-batch:repaired",
            successor=successor,
        )
        from app.agent_graph.contracts import SingleActionReasonCode

        return GraphCandidatePlan(
            candidate_evaluations=(candidate,),
            single_action_reason_code=SingleActionReasonCode.ONLY_GUARD_SATISFIED,
        )

    execution_count = 0

    async def execute(
        _context: GraphWorkContext,
        action: AllowedActionV1,
    ) -> GraphActionOutcome:
        nonlocal execution_count
        execution_count += 1
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=action.required_evidence,
        )

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="graph-worker-repair-budget",
        lease_seconds=60,
        supervisor=Supervisor("repair_analysis_batch"),
        candidate_provider=plan,
        executor=execute,
    )

    for expected_count in (1, 2, 3):
        assert await worker.run_once() is True
        async with database.session_factory() as session:
            state = await AgentGraphRepository(session).get_run_state_for_agent_run(
                run_id
            )
            assert state is not None
            assert state.replan_count == expected_count

    assert await worker.run_once() is True
    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        state = await AgentGraphRepository(session).get_run_state_for_agent_run(run_id)
        assert run is not None
        assert state is not None
        assert execution_count == 3
        assert state.replan_count == 3
        assert state.current_node == "blocked_model_error"
        assert run.status == "blocked_model_error"


@pytest.mark.asyncio
async def test_graph_termination_drains_through_report_before_releasing_lock(
    database,
) -> None:
    task_id, run_id = await _start_graph_run(database)
    key = Fernet.generate_key()
    secret_ref = ""
    connection_id = None
    async with database.session_factory() as session:
        async with session.begin():
            conversation = AgentConversationRecord(
                tenant_id="school-graph-worker",
                created_by="operator-1",
                status="active",
                context={},
            )
            session.add(conversation)
            await session.flush()
            secret_ref = await EncryptedDatabaseSecretStore(
                session,
                fernet_key=key,
            ).put(
                tenant_id="school-graph-worker",
                payload={"app_key": "app", "app_secret": "secret"},
            )
            connection = ApiConnectionRecord(
                tenant_id="school-graph-worker",
                provider_id="dingtalk",
                display_name="终止前临时连接",
                scope="task_ephemeral",
                conversation_id=conversation.id,
                task_id=task_id,
                public_configuration={},
                secret_ref=secret_ref,
                manifest_version="1.0.0",
                adapter_version="1.0.0",
                capabilities={},
                visibility_summary={},
                state="active",
                created_by="operator-1",
                updated_by="operator-1",
            )
            session.add(connection)
            await session.flush()
            connection_id = connection.id
            await AgentSupervisorService(
                session,
                operator=OperatorContext(
                    operator_id="operator-1",
                    tenant_id="school-graph-worker",
                ),
            ).terminate(run_id=run_id, reason="operator_requested")

    from app.agent_graph.runtime import ProductionGraphCandidateProvider

    class SupervisorMustNotRun:
        async def decide_with_provenance(self, _context):
            raise AssertionError("termination path called the model Supervisor")

    async def execute(_context, action):
        return GraphActionOutcome(
            action_id=action.action_id,
            evidence_refs=action.required_evidence,
        )

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="graph-worker-termination",
        lease_seconds=60,
        supervisor=SupervisorMustNotRun(),
        candidate_provider=ProductionGraphCandidateProvider(
            database.session_factory
        ),
        executor=execute,
    )
    for _step in range(3):
        assert await worker.run_once() is True

    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        state = await AgentGraphRepository(session).get_run_state_for_agent_run(run_id)
        task = await session.get(ReconciliationTask, task_id)
        active_lock = await session.scalar(
            select(SchoolTaskLockRecord.id).where(
                SchoolTaskLockRecord.owner_run_id == run_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )
        assert run is not None
        assert state is not None
        assert task is not None
        assert run.status == "terminated"
        assert task.status == "terminated"
        assert state.current_node == "terminal"
        assert active_lock is None
        connection = await session.get(ApiConnectionRecord, connection_id)
        assert connection is not None
        assert connection.state == "disabled"
        assert connection.disabled_reason == "task_terminated"
        with pytest.raises(SecretReferenceError, match="unavailable"):
            await EncryptedDatabaseSecretStore(
                session,
                fernet_key=key,
            ).get(
                tenant_id="school-graph-worker",
                secret_ref=secret_ref,
            )


@pytest.mark.asyncio
async def test_production_candidates_audit_rejected_graph_templates(database) -> None:
    from uuid import uuid4

    from app.agent_graph.runtime import ProductionGraphCandidateProvider

    task_id, run_id = await _start_graph_run(database)
    async with database.session_factory() as session:
        state = await AgentGraphRepository(session).get_run_state_for_agent_run(run_id)
        run = await session.get(AgentRunRecord, run_id)
        assert state is not None
        assert run is not None
        context = GraphWorkContext(
            worker_id="candidate-audit-worker",
            run_id=run.id,
            task_id=task_id,
            tenant_id=run.tenant_id,
            graph_run_id=state.id,
            graph_version=state.graph_version,
            current_node=state.current_node,
            graph_cursor=state.cursor,
            attempt_count=run.attempt_count,
            lease_token=uuid4(),
        )

    plan = await ProductionGraphCandidateProvider(database.session_factory)(context)

    passed = {
        item.action.graph_action_kind
        for item in plan.candidate_evaluations
        if item.passed
    }
    rejected = {
        item.action.action_id: item.rejected_guard_codes
        for item in plan.candidate_evaluations
        if not item.passed
    }
    assert passed == {"inspect_authority"}
    assert rejected == {
        "inspect_target": ("server_order_deferred",),
        "normalize_ready_sources": ("source_inspection_incomplete",),
    }
    assert (
        plan.single_action_reason_code
        is SingleActionReasonCode.ONLY_GUARD_SATISFIED
    )


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

    with pytest.raises(AgentGraphLeaseLost) as caught:
        await asyncio.wait_for(worker_task, timeout=1)
    assert caught.value.reason == "run_claim_lost"
    assert executor_cancelled.is_set()


@pytest.mark.asyncio
async def test_graph_worker_reports_school_lock_loss(database) -> None:
    _task_id, run_id = await _start_graph_run(database)
    candidate = _candidate(
        "inspect_authority:page-1",
        graph_action_kind="inspect_authority",
        resource_id="authority:page-1",
        evidence="authority-inspection-v1",
        successor="inspect_sources",
    )

    async def plan(_context: GraphWorkContext) -> GraphCandidatePlan:
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
        worker_id="graph-worker-school-lock-fencing",
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
            lock = await session.scalar(
                select(SchoolTaskLockRecord).where(
                    SchoolTaskLockRecord.owner_run_id == run_id,
                    SchoolTaskLockRecord.active.is_(True),
                )
            )
            assert lock is not None
            lock.active = False

    with pytest.raises(AgentGraphLeaseLost) as caught:
        await asyncio.wait_for(worker_task, timeout=1)
    assert caught.value.reason == "school_lock_lost"
    assert executor_cancelled.is_set()

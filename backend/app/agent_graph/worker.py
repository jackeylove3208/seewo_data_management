import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_graph.actions import (
    build_allowed_action_set,
    validate_supervisor_decision,
)
from app.agent_graph.contracts import (
    AllowedActionV1,
    CandidateActionEvaluationV1,
    SingleActionReasonCode,
)
from app.agent_graph.guards import GraphGuardRejected, GraphGuardService
from app.agent_graph.repository import (
    AgentGraphNotFound,
    AgentGraphRepository,
    GraphCursorConflict,
)
from app.agent_graph.supervisor import build_supervisor_context
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase
from app.ai.graph_supervisor import GraphSupervisorAgent
from app.models.agent_runtime import SchoolTaskLockRecord


class AgentGraphLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class GraphWorkContext:
    worker_id: str
    run_id: UUID
    task_id: UUID
    tenant_id: str
    graph_run_id: UUID
    graph_version: str
    current_node: str
    graph_cursor: int
    attempt_count: int
    lease_token: UUID


@dataclass(frozen=True)
class GraphCandidatePlan:
    candidate_evaluations: Sequence[CandidateActionEvaluationV1]
    single_action_reason_code: SingleActionReasonCode | None = None


@dataclass(frozen=True)
class GraphActionOutcome:
    action_id: str
    evidence_refs: tuple[str, ...] = ()


GraphCandidateProvider = Callable[[GraphWorkContext], Awaitable[GraphCandidatePlan]]
GraphActionExecutor = Callable[
    [GraphWorkContext, AllowedActionV1],
    Awaitable[GraphActionOutcome],
]


class AgentGraphWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        worker_id: str,
        lease_seconds: int,
        supervisor: GraphSupervisorAgent,
        candidate_provider: GraphCandidateProvider,
        executor: GraphActionExecutor,
        heartbeat_interval_seconds: float | None = None,
        guard: GraphGuardService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._supervisor = supervisor
        self._candidate_provider = candidate_provider
        self._executor = executor
        self._heartbeat_interval_seconds = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else max(1.0, lease_seconds / 3)
        )
        self._guard = guard or GraphGuardService()

    async def run_once(self) -> bool:
        context = await self._claim()
        if context is None:
            return False
        heartbeat_stopped = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._maintain_heartbeat(context, heartbeat_stopped)
        )
        processing_task = asyncio.create_task(self._process_claimed(context))
        try:
            done, _pending = await asyncio.wait(
                {processing_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                heartbeat_error = heartbeat_task.exception()
                if heartbeat_error is not None:
                    raise heartbeat_error
                if not heartbeat_task.result():
                    raise AgentGraphLeaseLost(
                        f"Agent graph lease lost: {context.run_id}"
                    )
                raise RuntimeError("Agent graph heartbeat stopped before work completion")
            heartbeat_stopped.set()
            if not await heartbeat_task:
                raise AgentGraphLeaseLost(f"Agent graph lease lost: {context.run_id}")
            return await processing_task
        except BaseException:
            heartbeat_stopped.set()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            if not processing_task.done():
                processing_task.cancel()
            await asyncio.gather(processing_task, return_exceptions=True)
            await self._release_claim(context)
            raise
        finally:
            heartbeat_stopped.set()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            if not processing_task.done():
                processing_task.cancel()
            await asyncio.gather(processing_task, return_exceptions=True)

    async def _process_claimed(self, context: GraphWorkContext) -> bool:
        plan = await self._candidate_provider(context)
        action_set = build_allowed_action_set(
            plan.candidate_evaluations,
            single_action_reason_code=plan.single_action_reason_code,
        )
        async with self._session_factory() as session:
            async with session.begin():
                repository = AgentGraphRepository(session)
                state = await repository.get_run_state(
                    context.graph_run_id,
                    for_update=True,
                )
                run = await AgentRuntimeRepository(session).get_run(
                    context.run_id,
                    for_update=True,
                )
                if state is None or run is None:
                    raise AgentGraphNotFound("claimed graph run disappeared")
                self._validate_snapshot(context, state.current_node, state.cursor)
                candidate_record = await repository.record_candidate_set(
                    graph_run_id=state.id,
                    cursor=state.cursor,
                    candidate_evaluations=plan.candidate_evaluations,
                    action_set=action_set,
                )
                candidate_set_id = candidate_record.id
                supervisor_context = build_supervisor_context(
                    state,
                    run,
                    action_set,
                )

        supervisor_result = await self._supervisor.decide_with_provenance(
            supervisor_context
        )
        decision = validate_supervisor_decision(
            supervisor_context,
            supervisor_result.decision,
        )
        selected = next(
            action
            for action in action_set.allowed_actions
            if action.action_id == decision.action_id
        )
        self._guard.validate_action_path(
            graph_version=context.graph_version,
            current_node=context.current_node,
            action=selected,
        )
        async with self._session_factory() as session:
            async with session.begin():
                await AgentGraphRepository(session).record_decision(
                    candidate_set_id=candidate_set_id,
                    decision=decision,
                    model_provenance={
                        "provider": supervisor_result.provider,
                        "model": supervisor_result.model,
                        "request_id": supervisor_result.request_id,
                        "attempt_count": supervisor_result.attempt_count,
                    },
                )

        outcome = await self._executor(context, selected)
        if outcome.action_id != selected.action_id:
            raise GraphGuardRejected("executor returned another action")
        if not set(outcome.evidence_refs).issubset(selected.required_evidence):
            raise GraphGuardRejected("executor returned evidence outside action contract")
        await self._commit(context, selected, outcome)
        return True

    async def _claim(self) -> GraphWorkContext | None:
        async with self._session_factory() as session:
            async with session.begin():
                runtime = AgentRuntimeRepository(session)
                claimed = await runtime.claim_next_run(
                    worker_id=self._worker_id,
                    lease_seconds=self._lease_seconds,
                    phases=frozenset(AgentPhase),
                    workflow_versions=frozenset({"agent-graph-v1"}),
                )
                if claimed is None:
                    return None
                if claimed.lease_token is None:
                    raise RuntimeError("claimed Agent graph run has no lease token")
                state = await AgentGraphRepository(session).get_run_state_for_agent_run(
                    claimed.id
                )
                if state is None:
                    raise AgentGraphNotFound("Agent graph state is missing")
                lock = await session.scalar(
                    select(SchoolTaskLockRecord.id).where(
                        SchoolTaskLockRecord.tenant_id == claimed.tenant_id,
                        SchoolTaskLockRecord.owner_run_id == claimed.id,
                        SchoolTaskLockRecord.active.is_(True),
                    )
                )
                if lock is None:
                    raise GraphGuardRejected("school_lock_missing")
                return GraphWorkContext(
                    worker_id=self._worker_id,
                    run_id=claimed.id,
                    task_id=claimed.task_id,
                    tenant_id=claimed.tenant_id,
                    graph_run_id=state.id,
                    graph_version=state.graph_version,
                    current_node=state.current_node,
                    graph_cursor=state.cursor,
                    attempt_count=claimed.attempt_count,
                    lease_token=claimed.lease_token,
                )

    async def _maintain_heartbeat(
        self,
        context: GraphWorkContext,
        stopped: asyncio.Event,
    ) -> bool:
        while True:
            try:
                await asyncio.wait_for(
                    stopped.wait(),
                    timeout=self._heartbeat_interval_seconds,
                )
                return True
            except TimeoutError:
                pass
            async with self._session_factory() as session:
                async with session.begin():
                    runtime = AgentRuntimeRepository(session)
                    claim_is_owned = await runtime.heartbeat_run_claim(
                        context.run_id,
                        worker_id=context.worker_id,
                        lease_token=context.lease_token,
                        lease_seconds=self._lease_seconds,
                    )
                    lock_is_owned = claim_is_owned and await runtime.heartbeat_school_lock(
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                    )
            if not claim_is_owned or not lock_is_owned:
                return False

    async def _commit(
        self,
        context: GraphWorkContext,
        action: AllowedActionV1,
        outcome: GraphActionOutcome,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                runtime = AgentRuntimeRepository(session)
                run = await runtime.get_run(context.run_id, for_update=True)
                state = await AgentGraphRepository(session).get_run_state(
                    context.graph_run_id,
                    for_update=True,
                )
                if run is None or state is None:
                    raise AgentGraphNotFound("Agent graph state disappeared before commit")
                self._guard.validate_fencing(
                    expected_worker_id=context.worker_id,
                    expected_lease_token=context.lease_token,
                    expected_attempt_count=context.attempt_count,
                    persisted_worker_id=run.lease_owner,
                    persisted_lease_token=run.lease_token,
                    persisted_attempt_count=run.attempt_count,
                )
                self._validate_snapshot(context, state.current_node, state.cursor)
                active_lock = await session.scalar(
                    select(SchoolTaskLockRecord.id).where(
                        SchoolTaskLockRecord.tenant_id == context.tenant_id,
                        SchoolTaskLockRecord.owner_run_id == context.run_id,
                        SchoolTaskLockRecord.active.is_(True),
                    )
                )
                if active_lock is None:
                    raise GraphGuardRejected("school_lock_missing")
                transition = await AgentGraphRepository(session).record_transition(
                    state.id,
                    expected_cursor=context.graph_cursor,
                    from_node=context.current_node,
                    to_node=action.successor_node,
                    action_id=action.action_id,
                    guard_results={
                        "lease": "passed",
                        "school_lock": "passed",
                        "action_membership": "passed",
                        "evidence_refs": list(outcome.evidence_refs),
                    },
                    fencing_token=context.attempt_count,
                )
                run.phase = _coarse_phase(action.successor_node).value
                await runtime.append_event(
                    run.id,
                    "graph.transitioned",
                    {
                        "cursor": transition.cursor,
                        "node": transition.to_node,
                        "action_id": action.action_id,
                    },
                )
                await runtime.release_run_claim(
                    run.id,
                    worker_id=context.worker_id,
                    lease_token=context.lease_token,
                )

    async def _release_claim(self, context: GraphWorkContext) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await AgentRuntimeRepository(session).release_run_claim(
                    context.run_id,
                    worker_id=context.worker_id,
                    lease_token=context.lease_token,
                )

    @staticmethod
    def _validate_snapshot(
        context: GraphWorkContext,
        current_node: str,
        cursor: int,
    ) -> None:
        if current_node != context.current_node or cursor != context.graph_cursor:
            raise GraphCursorConflict("claimed graph state is stale")


def _coarse_phase(node: str) -> AgentPhase:
    if node in {
        "inspect_sources",
        "normalize_input_batches",
        "validate_input_contract",
        "abnormal_input_report",
    }:
        return AgentPhase.INGEST_AND_NORMALIZE
    if node in {"build_identity_index", "construct_identity_work"}:
        return AgentPhase.BUILD_IDENTITY_WORK
    if node in {
        "analyze_actionable_batches",
        "repair_analysis_batch",
    }:
        return AgentPhase.ANALYZE_BATCHES
    if node == "resolve_identity_conflicts":
        return AgentPhase.CLARIFY_IDENTITY_CONFLICTS
    if node in {"aggregate_risk", "wait_high_risk_approvals"}:
        return AgentPhase.AGGREGATE_RISK_AND_APPROVALS
    if node in {"compile_execution_plan", "preflight_execution", "wait_replan_confirmation"}:
        return AgentPhase.COMPILE_EXECUTION_PLAN
    if node in {
        "execute_ready_operations",
        "verify_operations",
        "execute_remaining_independent",
    }:
        return AgentPhase.EXECUTE_AND_VERIFY
    if node in {"generate_terminal_report", "termination_report"}:
        return AgentPhase.GENERATE_REPORT
    if node == "terminal":
        return AgentPhase.TERMINAL
    raise GraphGuardRejected(f"no coarse phase mapping for graph node: {node}")

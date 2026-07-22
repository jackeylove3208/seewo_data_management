import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime.repository import AgentRuntimeRepository, run_claim_is_active
from app.agent_runtime.state_machine import AgentPhase, AgentRunStatus


class AgentLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentWorkContext:
    worker_id: str
    run_id: UUID
    task_id: UUID
    tenant_id: str
    phase: AgentPhase
    attempt_count: int
    lease_token: UUID


AgentPhaseHandler = Callable[[AgentWorkContext], Awaitable["AgentWorkResult"]]


@dataclass(frozen=True)
class AgentWorkResult:
    next_phase: AgentPhase | None = None
    next_status: AgentRunStatus | None = None

    def __post_init__(self) -> None:
        if (self.next_phase is None) == (self.next_status is None):
            raise ValueError("exactly one Agent work result target is required")


class AgentWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        worker_id: str,
        lease_seconds: int,
        heartbeat_interval_seconds: float | None = None,
        handlers: Mapping[AgentPhase, AgentPhaseHandler],
    ) -> None:
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else max(1.0, lease_seconds / 3)
        )
        self.handlers = dict(handlers)

    async def _maintain_heartbeat(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        lease_token: UUID,
        stopped: asyncio.Event,
    ) -> bool:
        while True:
            try:
                await asyncio.wait_for(
                    stopped.wait(), timeout=self.heartbeat_interval_seconds
                )
                return True
            except TimeoutError:
                pass

            async with self.session_factory() as heartbeat_session:
                async with heartbeat_session.begin():
                    repository = AgentRuntimeRepository(heartbeat_session)
                    claim_is_owned = await repository.heartbeat_run_claim(
                        run_id,
                        worker_id=self.worker_id,
                        lease_token=lease_token,
                        lease_seconds=self.lease_seconds,
                    )
                    lock_is_owned = claim_is_owned and await repository.heartbeat_school_lock(
                        tenant_id=tenant_id,
                        run_id=run_id,
                    )
            if not claim_is_owned or not lock_is_owned:
                return False

    async def _run_fenced_handler(
        self,
        *,
        handler: AgentPhaseHandler,
        context: AgentWorkContext,
        heartbeat_task: asyncio.Task[bool],
    ) -> AgentWorkResult:
        handler_task: asyncio.Future[AgentWorkResult] = asyncio.ensure_future(
            handler(context)
        )
        try:
            waiters: set[asyncio.Future[Any]] = {handler_task, heartbeat_task}
            done, _pending = await asyncio.wait(
                waiters, return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat_task in done:
                if heartbeat_task.exception() is not None:
                    raise heartbeat_task.exception()  # type: ignore[misc]
                if heartbeat_task.result():
                    raise RuntimeError("Agent heartbeat stopped before handler completion")
                raise AgentLeaseLost(f"Agent run lease lost: {context.run_id}")
            return await handler_task
        finally:
            if not handler_task.done():
                handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)

    async def run_once(self) -> bool:
        async with self.session_factory() as claim_session:
            async with claim_session.begin():
                claimed = await AgentRuntimeRepository(claim_session).claim_next_run(
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                    phases=frozenset(self.handlers),
                )
                if claimed is None:
                    return False
                if claimed.lease_token is None:
                    raise RuntimeError("claimed Agent run has no lease token")
                context = AgentWorkContext(
                    worker_id=self.worker_id,
                    run_id=claimed.id,
                    task_id=claimed.task_id,
                    tenant_id=claimed.tenant_id,
                    phase=AgentPhase(claimed.phase),
                    attempt_count=claimed.attempt_count,
                    lease_token=claimed.lease_token,
                )

        heartbeat_stopped = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._maintain_heartbeat(
                run_id=context.run_id,
                tenant_id=context.tenant_id,
                lease_token=context.lease_token,
                stopped=heartbeat_stopped,
            )
        )
        try:
            result = await self._run_fenced_handler(
                handler=self.handlers[context.phase],
                context=context,
                heartbeat_task=heartbeat_task,
            )
            heartbeat_stopped.set()
            if not await heartbeat_task:
                raise AgentLeaseLost(f"Agent run lease lost: {context.run_id}")

            async with self.session_factory() as completion_session:
                async with completion_session.begin():
                    repository = AgentRuntimeRepository(completion_session)
                    run = await repository.get_run(context.run_id, for_update=True)
                    if not run_claim_is_active(
                        run,
                        worker_id=self.worker_id,
                        lease_token=context.lease_token,
                    ):
                        raise AgentLeaseLost(f"Agent run lease lost: {context.run_id}")
                    transitioned = await repository.transition_run(
                        context.run_id,
                        requested_phase=result.next_phase,
                        requested_status=result.next_status,
                    )
                    await repository.append_event(
                        context.run_id,
                        "phase.transitioned",
                        {"phase": transitioned.phase, "status": transitioned.status},
                    )
                    await repository.release_run_claim(
                        context.run_id,
                        worker_id=self.worker_id,
                        lease_token=context.lease_token,
                    )
            return True
        except BaseException:
            heartbeat_stopped.set()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            async with self.session_factory() as release_session:
                async with release_session.begin():
                    await AgentRuntimeRepository(release_session).release_run_claim(
                        context.run_id,
                        worker_id=self.worker_id,
                        lease_token=context.lease_token,
                    )
            raise
        finally:
            heartbeat_stopped.set()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

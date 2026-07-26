from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.state_machine import (
    AgentPhase,
    AgentRunKind,
    AgentRunStatus,
    transition,
)
from app.models.agent_runtime import (
    AgentCheckpointRecord,
    AgentConversationMessageRecord,
    AgentConversationRecord,
    AgentFailureRecord,
    AgentRunRecord,
    AgentTaskEventRecord,
    SchoolTaskLockRecord,
)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def run_claim_is_active(
    run: AgentRunRecord | None,
    *,
    worker_id: str,
    lease_token: UUID,
    now: datetime | None = None,
) -> bool:
    checked_at = now or datetime.now(UTC)
    return bool(
        run is not None
        and run.lease_owner == worker_id
        and run.lease_token == lease_token
        and run.lease_expires_at is not None
        and _as_utc(run.lease_expires_at) >= checked_at
    )


class SchoolLockConflict(RuntimeError):
    def __init__(self, owner_task_id: UUID) -> None:
        self.owner_task_id = owner_task_id
        super().__init__(f"school already has an active task: {owner_task_id}")


class CheckpointConflict(RuntimeError):
    pass


class AgentRunNotFound(LookupError):
    pass


class ConversationResetConflict(RuntimeError):
    def __init__(self, owner_task_id: UUID) -> None:
        self.owner_task_id = owner_task_id
        super().__init__(f"active school task blocks conversation reset: {owner_task_id}")


class AgentRuntimeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_conversation(
        self,
        *,
        tenant_id: str,
        created_by: str,
    ) -> AgentConversationRecord:
        record = AgentConversationRecord(
            id=uuid4(), tenant_id=tenant_id, created_by=created_by, context={}
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_or_create_conversation(
        self,
        *,
        tenant_id: str,
        created_by: str,
    ) -> AgentConversationRecord:
        current = await self.get_current_conversation(
            tenant_id=tenant_id,
            created_by=created_by,
        )
        if current is not None:
            return current
        try:
            async with self.session.begin_nested():
                return await self.create_conversation(
                    tenant_id=tenant_id,
                    created_by=created_by,
                )
        except IntegrityError:
            current = await self.get_current_conversation(
                tenant_id=tenant_id,
                created_by=created_by,
            )
            if current is None:
                raise
            return current

    async def reset_conversation(
        self,
        *,
        tenant_id: str,
        created_by: str,
        idempotency_key: str,
    ) -> AgentConversationRecord:
        await self._lock_conversation_scope(
            tenant_id=tenant_id,
            created_by=created_by,
        )
        repeated = await self.session.scalar(
            select(AgentConversationRecord).where(
                AgentConversationRecord.tenant_id == tenant_id,
                AgentConversationRecord.created_by == created_by,
                AgentConversationRecord.reset_idempotency_key == idempotency_key,
            )
        )
        if repeated is not None:
            return repeated
        active_lock = await self.session.scalar(
            select(SchoolTaskLockRecord)
            .where(
                SchoolTaskLockRecord.tenant_id == tenant_id,
                SchoolTaskLockRecord.active.is_(True),
            )
            .with_for_update()
        )
        if active_lock is not None:
            raise ConversationResetConflict(active_lock.owner_task_id)
        await self.session.execute(
            delete(AgentConversationRecord).where(
                AgentConversationRecord.tenant_id == tenant_id,
                AgentConversationRecord.created_by == created_by,
            )
        )
        await self.session.flush()
        record = AgentConversationRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            created_by=created_by,
            context={},
            reset_idempotency_key=idempotency_key,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def _lock_conversation_scope(
        self,
        *,
        tenant_id: str,
        created_by: str,
    ) -> None:
        bind = self.session.bind
        if bind is None or bind.dialect.name != "postgresql":
            return
        digest = sha256(f"{tenant_id}\0{created_by}".encode()).digest()
        lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
        await self.session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def get_active_conversation(
        self,
        conversation_id: UUID,
        *,
        tenant_id: str,
    ) -> AgentConversationRecord | None:
        return cast(
            AgentConversationRecord | None,
            await self.session.scalar(
                select(AgentConversationRecord).where(
                    AgentConversationRecord.id == conversation_id,
                    AgentConversationRecord.tenant_id == tenant_id,
                    AgentConversationRecord.status == "active",
                )
            ),
        )

    async def get_current_conversation(
        self,
        *,
        tenant_id: str,
        created_by: str,
    ) -> AgentConversationRecord | None:
        lock_owner = await self.session.scalar(
            select(AgentConversationRecord)
            .join(
                AgentRunRecord,
                AgentRunRecord.conversation_id == AgentConversationRecord.id,
            )
            .where(
                AgentConversationRecord.tenant_id == tenant_id,
                AgentConversationRecord.created_by == created_by,
                AgentConversationRecord.status == "active",
                AgentRunRecord.tenant_id == tenant_id,
                ~AgentRunRecord.status.in_(("completed", "terminated", "failed")),
            )
            .order_by(
                AgentRunRecord.created_at.desc(),
                AgentRunRecord.id.desc(),
            )
            .limit(1)
        )
        if lock_owner is not None:
            return lock_owner
        return cast(
            AgentConversationRecord | None,
            await self.session.scalar(
                select(AgentConversationRecord)
                .where(
                    AgentConversationRecord.tenant_id == tenant_id,
                    AgentConversationRecord.created_by == created_by,
                    AgentConversationRecord.status == "active",
                )
                .order_by(
                    AgentConversationRecord.created_at.desc(),
                    AgentConversationRecord.id.desc(),
                )
                .limit(1)
            ),
        )

    async def append_conversation_message(
        self,
        *,
        conversation_id: UUID,
        tenant_id: str,
        role: str,
        text: str,
        kind: str = "normal",
    ) -> AgentConversationMessageRecord:
        conversation = await self.session.scalar(
            select(AgentConversationRecord)
            .where(
                AgentConversationRecord.id == conversation_id,
                AgentConversationRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if conversation is None:
            raise LookupError(f"conversation not found: {conversation_id}")
        last_sequence = await self.session.scalar(
            select(func.max(AgentConversationMessageRecord.sequence)).where(
                AgentConversationMessageRecord.conversation_id == conversation_id
            )
        )
        record = AgentConversationMessageRecord(
            id=uuid4(),
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            sequence=int(last_sequence or 0) + 1,
            role=role,
            kind=kind,
            text=text,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_conversation_messages(
        self,
        *,
        conversation_id: UUID,
        tenant_id: str,
    ) -> tuple[AgentConversationMessageRecord, ...]:
        return tuple(
            await self.session.scalars(
                select(AgentConversationMessageRecord)
                .where(
                    AgentConversationMessageRecord.conversation_id == conversation_id,
                    AgentConversationMessageRecord.tenant_id == tenant_id,
                )
                .order_by(
                    AgentConversationMessageRecord.sequence,
                    AgentConversationMessageRecord.id,
                )
            )
        )

    async def create_run(
        self,
        *,
        task_id: UUID,
        tenant_id: str,
        conversation_id: UUID | None,
        kind: AgentRunKind,
        workflow_version: str = "new-agent-v1",
    ) -> AgentRunRecord:
        record = AgentRunRecord(
            id=uuid4(),
            task_id=task_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            kind=kind.value,
            workflow_version=workflow_version,
            phase=AgentPhase.INTENT_CONFIRMED.value,
            status=AgentRunStatus.PENDING.value,
            version=1,
            attempt_count=0,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_run(self, run_id: UUID, *, for_update: bool = False) -> AgentRunRecord | None:
        statement = select(AgentRunRecord).where(AgentRunRecord.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(AgentRunRecord | None, await self.session.scalar(statement))

    async def claim_next_run(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        phases: frozenset[AgentPhase],
        workflow_versions: frozenset[str] = frozenset({"new-agent-v1"}),
    ) -> AgentRunRecord | None:
        if not phases or not workflow_versions:
            return None
        now = datetime.now(UTC)
        run = await self.session.scalar(
            select(AgentRunRecord)
            .where(
                AgentRunRecord.status == AgentRunStatus.RUNNING.value,
                AgentRunRecord.phase.in_(phase.value for phase in phases),
                AgentRunRecord.workflow_version.in_(workflow_versions),
                or_(
                    AgentRunRecord.lease_expires_at.is_(None),
                    AgentRunRecord.lease_expires_at < now,
                ),
            )
            .order_by(AgentRunRecord.updated_at, AgentRunRecord.id)
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        run.lease_owner = worker_id
        run.lease_token = uuid4()
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.heartbeat_at = now
        run.attempt_count += 1
        run.updated_at = now
        await self.session.flush()
        return run

    async def heartbeat_run_claim(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        lease_token: UUID,
        lease_seconds: int,
    ) -> bool:
        run = await self.get_run(run_id, for_update=True)
        now = datetime.now(UTC)
        if not run_claim_is_active(
            run, worker_id=worker_id, lease_token=lease_token, now=now
        ):
            return False
        assert run is not None
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.updated_at = now
        await self.session.flush()
        return True

    async def release_run_claim(
        self, run_id: UUID, *, worker_id: str, lease_token: UUID
    ) -> bool:
        run = await self.get_run(run_id, for_update=True)
        if (
            run is None
            or run.lease_owner != worker_id
            or run.lease_token != lease_token
        ):
            return False
        run.lease_owner = None
        run.lease_token = None
        run.lease_expires_at = None
        run.updated_at = datetime.now(UTC)
        await self.session.flush()
        return True

    async def get_run_for_task(
        self, task_id: UUID, *, for_update: bool = False
    ) -> AgentRunRecord | None:
        statement = select(AgentRunRecord).where(AgentRunRecord.task_id == task_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(AgentRunRecord | None, await self.session.scalar(statement))

    async def append_event(
        self,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> AgentTaskEventRecord:
        run = await self.get_run(run_id, for_update=True)
        if run is None:
            raise AgentRunNotFound(str(run_id))
        sequence = (
            await self.session.scalar(
                select(func.max(AgentTaskEventRecord.sequence)).where(
                    AgentTaskEventRecord.run_id == run_id
                )
            )
            or 0
        ) + 1
        event = AgentTaskEventRecord(
            id=uuid4(),
            run_id=run_id,
            tenant_id=run.tenant_id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> tuple[AgentTaskEventRecord, ...]:
        rows = await self.session.scalars(
            select(AgentTaskEventRecord)
            .where(
                AgentTaskEventRecord.run_id == run_id,
                AgentTaskEventRecord.sequence > after_sequence,
            )
            .order_by(AgentTaskEventRecord.sequence)
            .limit(min(max(limit, 1), 500))
        )
        return tuple(rows)

    async def save_checkpoint(
        self,
        run_id: UUID,
        *,
        phase: AgentPhase,
        checkpoint_key: str,
        input_hash: str,
        payload: dict[str, Any],
    ) -> AgentCheckpointRecord:
        existing = await self.session.scalar(
            select(AgentCheckpointRecord).where(
                AgentCheckpointRecord.run_id == run_id,
                AgentCheckpointRecord.phase == phase.value,
                AgentCheckpointRecord.checkpoint_key == checkpoint_key,
            )
        )
        if existing is not None:
            if existing.input_hash != input_hash:
                raise CheckpointConflict("checkpoint input hash changed")
            return existing
        run = await self.get_run(run_id)
        if run is None:
            raise AgentRunNotFound(str(run_id))
        checkpoint = AgentCheckpointRecord(
            id=uuid4(),
            run_id=run_id,
            tenant_id=run.tenant_id,
            phase=phase.value,
            checkpoint_key=checkpoint_key,
            input_hash=input_hash,
            payload=payload,
        )
        self.session.add(checkpoint)
        await self.session.flush()
        return checkpoint

    async def get_checkpoint(
        self,
        run_id: UUID,
        *,
        phase: AgentPhase,
        checkpoint_key: str,
    ) -> AgentCheckpointRecord | None:
        return cast(
            AgentCheckpointRecord | None,
            await self.session.scalar(
                select(AgentCheckpointRecord).where(
                    AgentCheckpointRecord.run_id == run_id,
                    AgentCheckpointRecord.phase == phase.value,
                    AgentCheckpointRecord.checkpoint_key == checkpoint_key,
                )
            ),
        )

    async def record_failure(
        self,
        run_id: UUID,
        *,
        phase: AgentPhase,
        code: str,
        safe_message: str,
        attempt_count: int,
        gateway_request_id: str | None = None,
    ) -> AgentFailureRecord:
        run = await self.get_run(run_id)
        if run is None:
            raise AgentRunNotFound(str(run_id))
        failure = AgentFailureRecord(
            id=uuid4(),
            run_id=run_id,
            tenant_id=run.tenant_id,
            phase=phase.value,
            code=code,
            safe_message=safe_message,
            gateway_request_id=gateway_request_id,
            attempt_count=attempt_count,
            details={},
        )
        self.session.add(failure)
        await self.session.flush()
        return failure

    async def acquire_school_lock(
        self,
        *,
        tenant_id: str,
        task_id: UUID,
        run_id: UUID,
    ) -> SchoolTaskLockRecord:
        existing = await self.session.scalar(
            select(SchoolTaskLockRecord)
            .where(
                SchoolTaskLockRecord.tenant_id == tenant_id,
                SchoolTaskLockRecord.active.is_(True),
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.owner_task_id == task_id and existing.owner_run_id == run_id:
                return existing
            raise SchoolLockConflict(existing.owner_task_id)
        now = datetime.now(UTC)
        lock = SchoolTaskLockRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            owner_task_id=task_id,
            owner_run_id=run_id,
            active=True,
            acquired_at=now,
            heartbeat_at=now,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(lock)
                await self.session.flush()
        except IntegrityError as error:
            owner = await self.session.scalar(
                select(SchoolTaskLockRecord).where(
                    SchoolTaskLockRecord.tenant_id == tenant_id,
                    SchoolTaskLockRecord.active.is_(True),
                )
            )
            if owner is not None:
                raise SchoolLockConflict(owner.owner_task_id) from error
            raise
        return lock

    async def release_school_lock(
        self,
        *,
        tenant_id: str,
        run_id: UUID,
        reason: str,
    ) -> SchoolTaskLockRecord:
        lock = await self.session.scalar(
            select(SchoolTaskLockRecord)
            .where(
                SchoolTaskLockRecord.tenant_id == tenant_id,
                SchoolTaskLockRecord.owner_run_id == run_id,
                SchoolTaskLockRecord.active.is_(True),
            )
            .with_for_update()
        )
        if lock is None:
            raise AgentRunNotFound(f"active lock for {run_id}")
        lock.active = False
        lock.released_at = datetime.now(UTC)
        lock.release_reason = reason
        await self.session.flush()
        return lock

    async def heartbeat_school_lock(
        self,
        *,
        tenant_id: str,
        run_id: UUID,
    ) -> bool:
        lock = await self.session.scalar(
            select(SchoolTaskLockRecord).where(
                SchoolTaskLockRecord.tenant_id == tenant_id,
                SchoolTaskLockRecord.owner_run_id == run_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )
        if lock is None:
            return False
        lock.heartbeat_at = datetime.now(UTC)
        await self.session.flush()
        return True

    async def transition_run(
        self,
        run_id: UUID,
        *,
        requested_phase: AgentPhase | None = None,
        requested_status: AgentRunStatus | None = None,
    ) -> AgentRunRecord:
        run = await self.get_run(run_id, for_update=True)
        if run is None:
            raise AgentRunNotFound(str(run_id))
        result = transition(
            kind=AgentRunKind(run.kind),
            current_phase=AgentPhase(run.phase),
            current_status=AgentRunStatus(run.status),
            requested_phase=requested_phase,
            requested_status=requested_status,
        )
        run.phase = result.phase.value
        run.status = result.status.value
        run.version += 1
        run.updated_at = datetime.now(UTC)
        await self.session.flush()
        return run

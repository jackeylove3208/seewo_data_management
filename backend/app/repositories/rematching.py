from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rematching import (
    EntityRematchCandidateEdgeRecord,
    EntityRematchJobRecord,
    EntityRematchWorkItemRecord,
)

_TERMINAL_STATUSES = {"ai_recovered", "no_match", "manual_review", "conflict", "failed"}


@dataclass(frozen=True)
class RematchCandidateDraft:
    candidate_entity_id: UUID
    candidate_role: str
    rank: int
    representation_version: str
    vector_score: float | None = None
    lexical_score: float | None = None
    evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class RematchWorkItemDraft:
    entity_type: str
    focal_entity_id: UUID
    focal_role: str
    candidate_set_hash: str
    candidates: tuple[RematchCandidateDraft, ...]


class EntityRematchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_tenant(self, job_id: UUID, tenant_id: str) -> EntityRematchJobRecord | None:
        return cast(
            EntityRematchJobRecord | None,
            await self.session.scalar(
                select(EntityRematchJobRecord).where(
                    EntityRematchJobRecord.id == job_id,
                    EntityRematchJobRecord.tenant_id == tenant_id,
                )
            ),
        )

    async def current_for_task(
        self, task_id: UUID, tenant_id: str
    ) -> EntityRematchJobRecord | None:
        return cast(
            EntityRematchJobRecord | None,
            await self.session.scalar(
                select(EntityRematchJobRecord)
                .where(
                    EntityRematchJobRecord.task_id == task_id,
                    EntityRematchJobRecord.tenant_id == tenant_id,
                )
                .order_by(
                    EntityRematchJobRecord.created_at.desc(), EntityRematchJobRecord.id.desc()
                )
            ),
        )

    async def create_or_get(
        self,
        *,
        task_id: UUID,
        tenant_id: str,
        requested_by: str,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
        idempotency_key: str,
        policy_version: str,
        items: tuple[RematchWorkItemDraft, ...],
    ) -> EntityRematchJobRecord:
        existing = await self._by_idempotency(task_id, tenant_id, idempotency_key)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        job = EntityRematchJobRecord(
            task_id=task_id,
            tenant_id=tenant_id,
            requested_by=requested_by,
            source_snapshot_id=source_snapshot_id,
            target_snapshot_id=target_snapshot_id,
            policy_version=policy_version,
            idempotency_key=idempotency_key,
            status="queued",
            total=len(items),
            indexed=len(items),
            event_cursor=1,
            created_at=now,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(job)
                await self.session.flush()
                for draft in items:
                    item = EntityRematchWorkItemRecord(
                        job_id=job.id,
                        tenant_id=tenant_id,
                        entity_type=draft.entity_type,
                        focal_entity_id=draft.focal_entity_id,
                        focal_role=draft.focal_role,
                        candidate_set_hash=draft.candidate_set_hash,
                        policy_version=policy_version,
                        status="queued",
                        available_at=now,
                        created_at=now,
                    )
                    self.session.add(item)
                    await self.session.flush()
                    self.session.add_all(
                        EntityRematchCandidateEdgeRecord(
                            job_id=job.id,
                            work_item_id=item.id,
                            tenant_id=tenant_id,
                            focal_entity_id=draft.focal_entity_id,
                            focal_role=draft.focal_role,
                            candidate_entity_id=candidate.candidate_entity_id,
                            candidate_role=candidate.candidate_role,
                            rank=candidate.rank,
                            vector_score=candidate.vector_score,
                            lexical_score=candidate.lexical_score,
                            representation_version=candidate.representation_version,
                            evidence=candidate.evidence or {},
                            created_at=now,
                        )
                        for candidate in draft.candidates
                    )
                await self.session.flush()
        except IntegrityError:
            existing = await self._by_idempotency(task_id, tenant_id, idempotency_key)
            if existing is None:
                raise
            return existing
        return job

    async def _by_idempotency(
        self, task_id: UUID, tenant_id: str, idempotency_key: str
    ) -> EntityRematchJobRecord | None:
        return cast(
            EntityRematchJobRecord | None,
            await self.session.scalar(
                select(EntityRematchJobRecord).where(
                    EntityRematchJobRecord.task_id == task_id,
                    EntityRematchJobRecord.tenant_id == tenant_id,
                    EntityRematchJobRecord.idempotency_key == idempotency_key,
                )
            ),
        )

    async def work_items(
        self, job_id: UUID, tenant_id: str
    ) -> tuple[EntityRematchWorkItemRecord, ...]:
        return tuple(
            await self.session.scalars(
                select(EntityRematchWorkItemRecord)
                .where(
                    EntityRematchWorkItemRecord.job_id == job_id,
                    EntityRematchWorkItemRecord.tenant_id == tenant_id,
                )
                .order_by(EntityRematchWorkItemRecord.created_at, EntityRematchWorkItemRecord.id)
            )
        )

    async def get_item_for_tenant(
        self, item_id: UUID, tenant_id: str
    ) -> EntityRematchWorkItemRecord | None:
        return cast(
            EntityRematchWorkItemRecord | None,
            await self.session.scalar(
                select(EntityRematchWorkItemRecord).where(
                    EntityRematchWorkItemRecord.id == item_id,
                    EntityRematchWorkItemRecord.tenant_id == tenant_id,
                )
            ),
        )

    async def candidate_edges(
        self, item_id: UUID, tenant_id: str
    ) -> tuple[EntityRematchCandidateEdgeRecord, ...]:
        return tuple(
            await self.session.scalars(
                select(EntityRematchCandidateEdgeRecord)
                .where(
                    EntityRematchCandidateEdgeRecord.work_item_id == item_id,
                    EntityRematchCandidateEdgeRecord.tenant_id == tenant_id,
                )
                .order_by(EntityRematchCandidateEdgeRecord.rank)
            )
        )

    async def claim_next(
        self,
        job_id: UUID,
        tenant_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> EntityRematchWorkItemRecord | None:
        claimed_at = now or datetime.now(UTC)
        item = await self.session.scalar(
            select(EntityRematchWorkItemRecord)
            .where(
                EntityRematchWorkItemRecord.job_id == job_id,
                EntityRematchWorkItemRecord.tenant_id == tenant_id,
                or_(
                    and_(
                        EntityRematchWorkItemRecord.status.in_(("queued", "retry_wait")),
                        EntityRematchWorkItemRecord.available_at <= claimed_at,
                        or_(
                            EntityRematchWorkItemRecord.lease_expires_at.is_(None),
                            EntityRematchWorkItemRecord.lease_expires_at < claimed_at,
                        ),
                    ),
                    and_(
                        EntityRematchWorkItemRecord.status == "running",
                        EntityRematchWorkItemRecord.lease_expires_at < claimed_at,
                    ),
                ),
            )
            .order_by(EntityRematchWorkItemRecord.available_at, EntityRematchWorkItemRecord.id)
            .with_for_update(skip_locked=True)
        )
        if item is None:
            return None
        job = await self.session.scalar(
            select(EntityRematchJobRecord)
            .where(
                EntityRematchJobRecord.id == job_id,
                EntityRematchJobRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if job is None or job.cancel_requested:
            return None
        item.status = "running"
        item.attempt_count += 1
        item.lease_owner = worker_id
        item.lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
        item.heartbeat_at = claimed_at
        item.started_at = item.started_at or claimed_at
        if job.status == "queued":
            job.status = "running"
            job.started_at = claimed_at
        job.heartbeat_at = claimed_at
        job.event_cursor += 1
        await self.session.flush()
        return item

    async def claim_next_available(
        self, *, worker_id: str, lease_seconds: int
    ) -> EntityRematchWorkItemRecord | None:
        jobs = tuple(
            await self.session.execute(
                select(EntityRematchJobRecord.id, EntityRematchJobRecord.tenant_id)
                .where(
                    EntityRematchJobRecord.status.in_(("queued", "running")),
                    EntityRematchJobRecord.cancel_requested.is_(False),
                )
                .order_by(EntityRematchJobRecord.created_at, EntityRematchJobRecord.id)
            )
        )
        for job_id, tenant_id in jobs:
            item = await self.claim_next(
                job_id,
                tenant_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
            if item is not None:
                return item
        return None

    async def heartbeat(
        self,
        item_id: UUID,
        tenant_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        heartbeat_at = now or datetime.now(UTC)
        item = await self._locked_item(item_id, tenant_id)
        if not self._owns_active_lease(item, worker_id, heartbeat_at):
            return False
        assert item is not None
        item.lease_expires_at = heartbeat_at + timedelta(seconds=lease_seconds)
        item.heartbeat_at = heartbeat_at
        job = await self._locked_job(item.job_id, tenant_id)
        if job is not None:
            job.heartbeat_at = heartbeat_at
            job.event_cursor += 1
        await self.session.flush()
        return True

    async def schedule_retry(
        self,
        item_id: UUID,
        tenant_id: str,
        *,
        worker_id: str,
        base_delay_seconds: int,
        failure_code: str,
        now: datetime | None = None,
    ) -> EntityRematchWorkItemRecord:
        retry_at = now or datetime.now(UTC)
        item = await self._locked_item(item_id, tenant_id)
        if not self._owns_active_lease(item, worker_id, retry_at):
            raise ValueError("entity rematch work item lease is no longer owned by worker")
        assert item is not None
        if item.attempt_count >= item.max_attempts:
            return await self._finish_locked(
                item,
                tenant_id,
                outcome_status="failed",
                outcome={"decision": "manual_review", "reason": "重试次数已耗尽"},
                failure_code=failure_code,
                completed_at=retry_at,
            )
        delay = base_delay_seconds * (2 ** max(item.attempt_count - 1, 0))
        item.status = "retry_wait"
        item.available_at = retry_at + timedelta(seconds=delay)
        item.failure_code = failure_code
        item.lease_owner = None
        item.lease_expires_at = None
        job = await self._locked_job(item.job_id, tenant_id)
        if job is not None:
            job.event_cursor += 1
        await self.session.flush()
        return item

    async def complete_item(
        self,
        item_id: UUID,
        tenant_id: str,
        *,
        worker_id: str,
        outcome_status: str,
        outcome: dict[str, Any],
        failure_code: str | None = None,
        now: datetime | None = None,
    ) -> EntityRematchWorkItemRecord:
        if outcome_status not in _TERMINAL_STATUSES:
            raise ValueError("item completion requires a terminal outcome")
        completed_at = now or datetime.now(UTC)
        item = await self._locked_item(item_id, tenant_id)
        if item is None:
            raise LookupError(f"entity rematch work item not found: {item_id}")
        if item.status in _TERMINAL_STATUSES:
            if item.outcome_status == outcome_status and item.outcome == outcome:
                return item
            raise ValueError("entity rematch outcome is immutable")
        if not self._owns_active_lease(item, worker_id, completed_at):
            raise ValueError("entity rematch work item lease is no longer owned by worker")
        return await self._finish_locked(
            item,
            tenant_id,
            outcome_status=outcome_status,
            outcome=outcome,
            failure_code=failure_code,
            completed_at=completed_at,
        )

    async def _finish_locked(
        self,
        item: EntityRematchWorkItemRecord,
        tenant_id: str,
        *,
        outcome_status: str,
        outcome: dict[str, Any],
        failure_code: str | None,
        completed_at: datetime,
    ) -> EntityRematchWorkItemRecord:
        item.status = outcome_status
        item.outcome_status = outcome_status
        item.outcome = outcome
        item.failure_code = failure_code
        item.completed_at = completed_at
        item.heartbeat_at = completed_at
        item.lease_owner = None
        item.lease_expires_at = None
        await self.session.flush()
        await self.reconcile_counters(item.job_id, tenant_id, now=completed_at)
        return item

    async def reuse_outcome(self, item_id: UUID, tenant_id: str) -> bool:
        item = await self._locked_item(item_id, tenant_id)
        if item is None or item.status != "queued":
            return False
        previous = await self.session.scalar(
            select(EntityRematchWorkItemRecord)
            .where(
                EntityRematchWorkItemRecord.id != item.id,
                EntityRematchWorkItemRecord.tenant_id == tenant_id,
                EntityRematchWorkItemRecord.entity_type == item.entity_type,
                EntityRematchWorkItemRecord.focal_entity_id == item.focal_entity_id,
                EntityRematchWorkItemRecord.focal_role == item.focal_role,
                EntityRematchWorkItemRecord.candidate_set_hash == item.candidate_set_hash,
                EntityRematchWorkItemRecord.policy_version == item.policy_version,
                EntityRematchWorkItemRecord.outcome_status.in_(_TERMINAL_STATUSES),
            )
            .order_by(
                EntityRematchWorkItemRecord.completed_at.desc(),
                EntityRematchWorkItemRecord.id.desc(),
            )
        )
        if previous is None or previous.outcome is None or previous.outcome_status is None:
            return False
        item.status = previous.outcome_status
        item.outcome_status = previous.outcome_status
        item.outcome = dict(previous.outcome)
        item.failure_code = previous.failure_code
        item.reused_from_item_id = previous.id
        item.completed_at = datetime.now(UTC)
        await self.session.flush()
        await self.reconcile_counters(item.job_id, tenant_id)
        return True

    async def recover_expired_leases(self, tenant_id: str, *, now: datetime | None = None) -> int:
        recovered_at = now or datetime.now(UTC)
        items = tuple(
            await self.session.scalars(
                select(EntityRematchWorkItemRecord)
                .where(
                    EntityRematchWorkItemRecord.tenant_id == tenant_id,
                    EntityRematchWorkItemRecord.status == "running",
                    EntityRematchWorkItemRecord.lease_expires_at < recovered_at,
                )
                .with_for_update(skip_locked=True)
            )
        )
        touched_jobs: set[UUID] = set()
        for item in items:
            item.status = "queued"
            item.available_at = recovered_at
            item.lease_owner = None
            item.lease_expires_at = None
            item.failure_code = "lease_expired"
            item.heartbeat_at = recovered_at
            touched_jobs.add(item.job_id)
        for job_id in touched_jobs:
            job = await self._locked_job(job_id, tenant_id)
            if job is not None:
                job.heartbeat_at = recovered_at
                job.event_cursor += 1
        await self.session.flush()
        return len(items)

    async def cancel(self, job_id: UUID, tenant_id: str) -> EntityRematchJobRecord | None:
        items = tuple(
            await self.session.scalars(
                select(EntityRematchWorkItemRecord)
                .where(
                    EntityRematchWorkItemRecord.job_id == job_id,
                    EntityRematchWorkItemRecord.tenant_id == tenant_id,
                    ~EntityRematchWorkItemRecord.status.in_(_TERMINAL_STATUSES | {"canceled"}),
                )
                .with_for_update()
            )
        )
        job = await self._locked_job(job_id, tenant_id)
        if job is None:
            return None
        now = datetime.now(UTC)
        for item in items:
            item.status = "canceled"
            item.completed_at = now
            item.lease_owner = None
            item.lease_expires_at = None
        job.cancel_requested = True
        job.status = "canceled"
        job.completed_at = now
        job.event_cursor += 1
        await self.session.flush()
        return job

    async def reconcile_counters(
        self, job_id: UUID, tenant_id: str, *, now: datetime | None = None
    ) -> EntityRematchJobRecord | None:
        items = tuple(
            await self.session.scalars(
                select(EntityRematchWorkItemRecord)
                .where(
                    EntityRematchWorkItemRecord.job_id == job_id,
                    EntityRematchWorkItemRecord.tenant_id == tenant_id,
                )
                .with_for_update()
            )
        )
        job = await self._locked_job(job_id, tenant_id)
        if job is None:
            return None
        counts = {
            status: sum(item.status == status for item in items) for status in _TERMINAL_STATUSES
        }
        job.ai_recovered = counts["ai_recovered"]
        job.no_match = counts["no_match"]
        job.manual_review = counts["manual_review"]
        job.conflict = counts["conflict"]
        job.failed = counts["failed"]
        job.processed = sum(counts.values())
        if job.processed >= job.total and not job.cancel_requested:
            job.status = "completed_with_failures" if job.failed else "completed"
            job.completed_at = now or datetime.now(UTC)
        job.event_cursor += 1
        await self.session.flush()
        return job

    async def _locked_item(
        self, item_id: UUID, tenant_id: str
    ) -> EntityRematchWorkItemRecord | None:
        return cast(
            EntityRematchWorkItemRecord | None,
            await self.session.scalar(
                select(EntityRematchWorkItemRecord)
                .where(
                    EntityRematchWorkItemRecord.id == item_id,
                    EntityRematchWorkItemRecord.tenant_id == tenant_id,
                )
                .with_for_update()
            ),
        )

    async def _locked_job(self, job_id: UUID, tenant_id: str) -> EntityRematchJobRecord | None:
        return cast(
            EntityRematchJobRecord | None,
            await self.session.scalar(
                select(EntityRematchJobRecord)
                .where(
                    EntityRematchJobRecord.id == job_id,
                    EntityRematchJobRecord.tenant_id == tenant_id,
                )
                .with_for_update()
            ),
        )

    @staticmethod
    def _owns_active_lease(
        item: EntityRematchWorkItemRecord | None, worker_id: str, now: datetime
    ) -> bool:
        if item is None or item.status != "running" or item.lease_owner != worker_id:
            return False
        if item.lease_expires_at is None:
            return False
        expires_at = item.lease_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at >= now

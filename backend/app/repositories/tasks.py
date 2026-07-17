from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reconciliation import ReconciliationTask
from app.schemas.ingestion import SnapshotScope


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        scope: SnapshotScope,
        idempotency_key: str,
        request_hash: str,
    ) -> ReconciliationTask:
        task = ReconciliationTask(
            id=uuid4(),
            tenant_id=scope.tenant_id,
            scope_id=scope.scope_id,
            snapshot_mode=scope.mode.value,
            entity_types=sorted(entity_type.value for entity_type in scope.entity_types),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        self.session.add(task)
        return task

    async def get(self, task_id: UUID) -> ReconciliationTask | None:
        return await self.session.get(ReconciliationTask, task_id)

    async def get_for_update(self, task_id: UUID) -> ReconciliationTask | None:
        return cast(
            ReconciliationTask | None,
            await self.session.scalar(
                select(ReconciliationTask).where(ReconciliationTask.id == task_id).with_for_update()
            ),
        )

    async def find_by_idempotency_key(self, key: str) -> ReconciliationTask | None:
        return cast(
            ReconciliationTask | None,
            await self.session.scalar(
                select(ReconciliationTask).where(ReconciliationTask.idempotency_key == key)
            ),
        )

    async def mark_ready(self, task: ReconciliationTask) -> None:
        task.status = "ready"
        task.stage = "snapshots"

    async def mark_failed(self, task: ReconciliationTask, error: dict[str, object]) -> None:
        task.status = "failed"
        task.error = error

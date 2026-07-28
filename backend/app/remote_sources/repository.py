from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.remote_sources import RemoteSourceRecord


class RemoteSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register(
        self,
        *,
        tenant_id: str,
        created_by: str,
        conversation_id: UUID,
        original_url: str,
        display_origin: str,
    ) -> RemoteSourceRecord:
        record = RemoteSourceRecord(
            tenant_id=tenant_id,
            created_by=created_by,
            conversation_id=conversation_id,
            original_url=original_url,
            display_origin=display_origin,
            state="registered",
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_for_conversation(
        self,
        *,
        tenant_id: str,
        created_by: str,
        conversation_id: UUID,
    ) -> tuple[RemoteSourceRecord, ...]:
        return tuple(
            await self.session.scalars(
                select(RemoteSourceRecord)
                .where(
                    RemoteSourceRecord.tenant_id == tenant_id,
                    RemoteSourceRecord.created_by == created_by,
                    RemoteSourceRecord.conversation_id == conversation_id,
                    RemoteSourceRecord.state.in_(("registered", "materializing", "ready")),
                )
                .order_by(RemoteSourceRecord.created_at, RemoteSourceRecord.id)
            )
        )

    async def get_for_conversation(
        self,
        remote_source_id: UUID,
        *,
        tenant_id: str,
        created_by: str,
        conversation_id: UUID,
        for_update: bool = False,
    ) -> RemoteSourceRecord | None:
        statement = select(RemoteSourceRecord).where(
            RemoteSourceRecord.id == remote_source_id,
            RemoteSourceRecord.tenant_id == tenant_id,
            RemoteSourceRecord.created_by == created_by,
            RemoteSourceRecord.conversation_id == conversation_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(RemoteSourceRecord | None, await self.session.scalar(statement))

    async def get_for_task(
        self,
        *,
        tenant_id: str,
        task_id: UUID,
        for_update: bool = False,
    ) -> RemoteSourceRecord | None:
        statement = select(RemoteSourceRecord).where(
            RemoteSourceRecord.tenant_id == tenant_id,
            RemoteSourceRecord.task_id == task_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(RemoteSourceRecord | None, await self.session.scalar(statement))

    async def bind_to_task(
        self,
        record: RemoteSourceRecord,
        *,
        task_id: UUID,
    ) -> RemoteSourceRecord:
        if record.task_id is not None and record.task_id != task_id:
            raise ValueError("remote source is already bound to another task")
        record.task_id = task_id
        return record

    @staticmethod
    def mark_materializing(record: RemoteSourceRecord) -> None:
        if record.state not in {"registered", "materializing", "failed"}:
            raise ValueError("remote source cannot enter materializing state")
        record.state = "materializing"
        record.safe_problem_code = None
        record.updated_at = datetime.now(UTC)

    @staticmethod
    def mark_ready(
        record: RemoteSourceRecord,
        *,
        source_file_id: UUID,
        content_sha256: str,
        size_bytes: int,
        media_type: str,
        retrieved_at: datetime,
    ) -> None:
        record.state = "ready"
        record.source_file_id = source_file_id
        record.content_sha256 = content_sha256
        record.size_bytes = size_bytes
        record.media_type = media_type
        record.retrieved_at = retrieved_at
        record.safe_problem_code = None
        record.updated_at = datetime.now(UTC)

    @staticmethod
    def mark_failed(record: RemoteSourceRecord, *, safe_problem_code: str) -> None:
        record.state = "failed"
        record.safe_problem_code = safe_problem_code
        record.updated_at = datetime.now(UTC)

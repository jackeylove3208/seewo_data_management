from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_connectors import ApiAuthoritySourceRecord, ApiConnectionRecord


class ApiConnectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: ApiConnectionRecord) -> ApiConnectionRecord:
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_for_tenant(self, tenant_id: str) -> tuple[ApiConnectionRecord, ...]:
        rows = await self._session.scalars(
            select(ApiConnectionRecord)
            .where(ApiConnectionRecord.tenant_id == tenant_id)
            .order_by(ApiConnectionRecord.display_name, ApiConnectionRecord.id)
        )
        return tuple(rows)

    async def list_ephemeral_for_conversation(
        self,
        *,
        tenant_id: str,
        conversation_id: UUID,
    ) -> tuple[ApiConnectionRecord, ...]:
        rows = await self._session.scalars(
            select(ApiConnectionRecord)
            .where(
                ApiConnectionRecord.tenant_id == tenant_id,
                ApiConnectionRecord.scope == "task_ephemeral",
                ApiConnectionRecord.conversation_id == conversation_id,
                ApiConnectionRecord.task_id.is_(None),
                ApiConnectionRecord.credentials_revoked_at.is_(None),
            )
            .order_by(ApiConnectionRecord.display_name, ApiConnectionRecord.id)
        )
        return tuple(rows)

    async def get_for_tenant(
        self,
        connection_id: UUID,
        tenant_id: str,
    ) -> ApiConnectionRecord | None:
        return cast(
            ApiConnectionRecord | None,
            await self._session.scalar(
                select(ApiConnectionRecord).where(
                    ApiConnectionRecord.id == connection_id,
                    ApiConnectionRecord.tenant_id == tenant_id,
                )
            ),
        )

    async def get_by_display_name(
        self,
        *,
        tenant_id: str,
        display_name: str,
    ) -> ApiConnectionRecord | None:
        return cast(
            ApiConnectionRecord | None,
            await self._session.scalar(
                select(ApiConnectionRecord).where(
                    ApiConnectionRecord.tenant_id == tenant_id,
                    ApiConnectionRecord.display_name == display_name,
                )
            ),
        )

    async def has_bound_sources(self, connection_id: UUID, tenant_id: str) -> bool:
        source_id = await self._session.scalar(
            select(ApiAuthoritySourceRecord.id)
            .where(
                ApiAuthoritySourceRecord.connection_id == connection_id,
                ApiAuthoritySourceRecord.tenant_id == tenant_id,
            )
            .limit(1)
        )
        return source_id is not None

    async def delete(self, record: ApiConnectionRecord) -> None:
        await self._session.delete(record)
        await self._session.flush()

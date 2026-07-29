from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_connectors import AgentExternalIdentityBindingRecord


class ExternalIdentityRepositoryConflict(ValueError):
    pass


class AgentExternalIdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_tenant(
        self,
        *,
        tenant_id: str,
    ) -> tuple[AgentExternalIdentityBindingRecord, ...]:
        return tuple(
            await self._session.scalars(
                select(AgentExternalIdentityBindingRecord)
                .where(
                    AgentExternalIdentityBindingRecord.tenant_id == tenant_id
                )
                .order_by(
                    AgentExternalIdentityBindingRecord.confirmed_at.desc(),
                    AgentExternalIdentityBindingRecord.id,
                )
            )
        )

    async def active_for_scope(
        self,
        *,
        tenant_id: str,
        provider_id: str,
        connection_id: UUID,
        target_connector_id: str,
        entity_kinds: Sequence[str],
    ) -> tuple[AgentExternalIdentityBindingRecord, ...]:
        if not entity_kinds:
            return ()
        return tuple(
            await self._session.scalars(
                select(AgentExternalIdentityBindingRecord)
                .where(
                    AgentExternalIdentityBindingRecord.tenant_id == tenant_id,
                    AgentExternalIdentityBindingRecord.provider_id == provider_id,
                    AgentExternalIdentityBindingRecord.connection_id == connection_id,
                    AgentExternalIdentityBindingRecord.target_connector_id
                    == target_connector_id,
                    AgentExternalIdentityBindingRecord.entity_kind.in_(
                        tuple(entity_kinds)
                    ),
                    AgentExternalIdentityBindingRecord.status == "active",
                )
                .order_by(
                    AgentExternalIdentityBindingRecord.entity_kind,
                    AgentExternalIdentityBindingRecord.authority_stable_locator,
                    AgentExternalIdentityBindingRecord.id,
                )
            )
        )

    async def create_active(
        self,
        *,
        tenant_id: str,
        provider_id: str,
        connection_id: UUID,
        entity_kind: str,
        authority_stable_locator: str,
        target_connector_id: str,
        target_stable_locator: str,
        confirmed_by: str,
        evidence_hash: str,
    ) -> AgentExternalIdentityBindingRecord:
        competing = await self._session.scalar(
            select(AgentExternalIdentityBindingRecord).where(
                AgentExternalIdentityBindingRecord.tenant_id == tenant_id,
                AgentExternalIdentityBindingRecord.connection_id == connection_id,
                AgentExternalIdentityBindingRecord.entity_kind == entity_kind,
                AgentExternalIdentityBindingRecord.status == "active",
                or_(
                    AgentExternalIdentityBindingRecord.authority_stable_locator
                    == authority_stable_locator,
                    (
                        AgentExternalIdentityBindingRecord.target_connector_id
                        == target_connector_id
                    )
                    & (
                        AgentExternalIdentityBindingRecord.target_stable_locator
                        == target_stable_locator
                    ),
                ),
            )
        )
        if competing is not None:
            if (
                competing.provider_id == provider_id
                and competing.authority_stable_locator == authority_stable_locator
                and competing.target_connector_id == target_connector_id
                and competing.target_stable_locator == target_stable_locator
            ):
                return competing
            raise ExternalIdentityRepositoryConflict(
                "an active external identity binding already owns a locator"
            )
        latest_version = int(
            (
                await self._session.scalar(
                    select(
                        func.max(
                            AgentExternalIdentityBindingRecord.binding_version
                        )
                    ).where(
                        AgentExternalIdentityBindingRecord.tenant_id == tenant_id,
                        AgentExternalIdentityBindingRecord.provider_id == provider_id,
                        AgentExternalIdentityBindingRecord.connection_id
                        == connection_id,
                        AgentExternalIdentityBindingRecord.entity_kind == entity_kind,
                        AgentExternalIdentityBindingRecord.authority_stable_locator
                        == authority_stable_locator,
                    )
                )
            )
            or 0
        )
        record = AgentExternalIdentityBindingRecord(
            tenant_id=tenant_id,
            provider_id=provider_id,
            connection_id=connection_id,
            entity_kind=entity_kind,
            authority_stable_locator=authority_stable_locator,
            target_connector_id=target_connector_id,
            target_stable_locator=target_stable_locator,
            status="active",
            binding_version=latest_version + 1,
            confirmed_by=confirmed_by,
            confirmed_at=datetime.now(UTC),
            evidence_hash=evidence_hash,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(record)
                await self._session.flush()
        except IntegrityError as error:
            raise ExternalIdentityRepositoryConflict(
                "an active external identity binding already owns a locator"
            ) from error
        return record

    async def revoke(
        self,
        *,
        tenant_id: str,
        binding_id: UUID,
        revoked_by: str,
    ) -> AgentExternalIdentityBindingRecord | None:
        record = await self._session.scalar(
            select(AgentExternalIdentityBindingRecord)
            .where(
                AgentExternalIdentityBindingRecord.id == binding_id,
                AgentExternalIdentityBindingRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if record is None:
            return None
        if record.status == "active":
            record.status = "revoked"
            record.revoked_by = revoked_by
            record.revoked_at = datetime.now(UTC)
            await self._session.flush()
        return record

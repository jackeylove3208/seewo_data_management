from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mappings import EntityMapping
from app.schemas.matching import MatchDecision, MatchStatus


class MappingCardinalityError(ValueError):
    pass


class MappingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_confirmed(self, tenant_id: str, source_key: str) -> EntityMapping | None:
        return cast(
            EntityMapping | None,
            await self.session.scalar(
                select(EntityMapping).where(
                    EntityMapping.tenant_id == tenant_id,
                    EntityMapping.source_key == source_key,
                    EntityMapping.confirmed_by.is_not(None),
                    EntityMapping.revoked_at.is_(None),
                )
            ),
        )

    async def find_confirmed_many(
        self,
        tenant_id: str,
        source_keys: Sequence[str],
        *,
        batch_size: int = 500,
    ) -> dict[str, EntityMapping]:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        records: dict[str, EntityMapping] = {}
        for index in range(0, len(source_keys), batch_size):
            key_batch = source_keys[index : index + batch_size]
            rows = await self.session.scalars(
                select(EntityMapping).where(
                    EntityMapping.tenant_id == tenant_id,
                    EntityMapping.source_key.in_(key_batch),
                    EntityMapping.confirmed_by.is_not(None),
                    EntityMapping.revoked_at.is_(None),
                )
            )
            records.update((row.source_key, row) for row in rows)
        return records

    async def save_decision(
        self,
        *,
        task_id: UUID,
        tenant_id: str,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
        decision: MatchDecision,
    ) -> EntityMapping:
        current_decision = decision.model_copy(update={"confirmed_by": None})
        record = self._record(
            task_id=task_id,
            tenant_id=tenant_id,
            source_snapshot_id=source_snapshot_id,
            target_snapshot_id=target_snapshot_id,
            decision=current_decision,
        )
        self.session.add(record)
        return record

    async def confirm(
        self,
        *,
        task_id: UUID,
        tenant_id: str,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
        decision: MatchDecision,
        confirmed_by: str,
    ) -> EntityMapping:
        if decision.target_key is None or decision.target_entity_id is None:
            raise ValueError("confirmed mappings require a target")
        existing_source = await self.find_confirmed(tenant_id, decision.source_key)
        if existing_source is not None:
            if existing_source.target_key == decision.target_key:
                return existing_source
            raise MappingCardinalityError("source already has an active confirmed target")
        existing_target = await self._find_confirmed_target(tenant_id, decision.target_key)
        if existing_target is not None:
            raise MappingCardinalityError("target already has an active confirmed source")

        confirmed = decision.model_copy(
            update={"status": MatchStatus.ACCEPTED, "confirmed_by": confirmed_by}
        )
        record = self._record(
            task_id=task_id,
            tenant_id=tenant_id,
            source_snapshot_id=source_snapshot_id,
            target_snapshot_id=target_snapshot_id,
            decision=confirmed,
            confirmed_at=datetime.now(UTC),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
        except IntegrityError as error:
            raise MappingCardinalityError(
                "concurrent mapping confirmation violated active cardinality"
            ) from error
        return record

    async def revoke(
        self,
        mapping_id: UUID,
        *,
        revoked_by: str,
        reason: str,
    ) -> EntityMapping:
        record = await self.session.get(EntityMapping, mapping_id)
        if record is None:
            raise LookupError(f"mapping not found: {mapping_id}")
        if record.revoked_at is not None:
            return record
        record.revoked_at = datetime.now(UTC)
        record.revoked_by = revoked_by
        record.revocation_reason = reason
        await self.session.flush()
        return record

    async def _find_confirmed_target(
        self,
        tenant_id: str,
        target_key: str,
    ) -> EntityMapping | None:
        return cast(
            EntityMapping | None,
            await self.session.scalar(
                select(EntityMapping).where(
                    EntityMapping.tenant_id == tenant_id,
                    EntityMapping.target_key == target_key,
                    EntityMapping.confirmed_by.is_not(None),
                    EntityMapping.revoked_at.is_(None),
                )
            ),
        )

    @staticmethod
    def _record(
        *,
        task_id: UUID,
        tenant_id: str,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
        decision: MatchDecision,
        confirmed_at: datetime | None = None,
    ) -> EntityMapping:
        return EntityMapping(
            task_id=task_id,
            tenant_id=tenant_id,
            source_snapshot_id=source_snapshot_id,
            target_snapshot_id=target_snapshot_id,
            entity_type=decision.entity_type.value,
            source_entity_id=decision.source_entity_id,
            source_key=decision.source_key,
            target_entity_id=decision.target_entity_id,
            target_key=decision.target_key,
            method=decision.method.value if decision.method else None,
            status=decision.status.value,
            confidence=Decimal(str(decision.confidence)),
            evidence=[item.model_dump(mode="json") for item in decision.evidence],
            rule_version=decision.rule_version,
            confirmed_by=decision.confirmed_by,
            confirmed_at=confirmed_at,
        )

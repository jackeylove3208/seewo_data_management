from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.quality import MatchingQualityRecord


class MatchingQualityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(
        self,
        *,
        task_id: UUID,
        tenant_id: str,
        policy_version: str,
        mapping_versions: Sequence[str],
        result: dict[str, Any],
    ) -> MatchingQualityRecord:
        record = MatchingQualityRecord(
            task_id=task_id,
            tenant_id=tenant_id,
            policy_version=policy_version,
            mapping_versions=list(mapping_versions),
            result=result,
            evaluated_at=datetime.now(UTC),
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def latest(self, task_id: UUID, tenant_id: str) -> MatchingQualityRecord | None:
        return cast(
            MatchingQualityRecord | None,
            await self.session.scalar(
                select(MatchingQualityRecord)
                .where(
                    MatchingQualityRecord.task_id == task_id,
                    MatchingQualityRecord.tenant_id == tenant_id,
                )
                .order_by(
                    MatchingQualityRecord.evaluated_at.desc(),
                    MatchingQualityRecord.id.desc(),
                )
            ),
        )

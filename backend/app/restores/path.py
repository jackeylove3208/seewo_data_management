from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.executions import TargetVersionRecord


class VersionPathError(ValueError):
    pass


class VersionPathResolver:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def split(
        self,
        source_version_id: UUID,
        target_version_id: UUID,
    ) -> tuple[tuple[TargetVersionRecord, ...], tuple[TargetVersionRecord, ...]]:
        source = await self._chain(source_version_id)
        target = await self._chain(target_version_id)
        source_ids = {item.id for item in source}
        common = next((item for item in target if item.id in source_ids), None)
        if common is None:
            raise VersionPathError("target versions do not share an ancestor")
        backward_items: list[TargetVersionRecord] = []
        for item in source:
            if item.id == common.id:
                break
            backward_items.append(item)
        backward = tuple(backward_items)
        target_to_common = []
        for item in target:
            if item.id == common.id:
                break
            target_to_common.append(item)
        return backward, tuple(reversed(target_to_common))

    async def _chain(self, version_id: UUID) -> tuple[TargetVersionRecord, ...]:
        records: list[TargetVersionRecord] = []
        current = await self.session.get(TargetVersionRecord, version_id)
        while current is not None:
            records.append(current)
            if current.parent_version_id is None:
                break
            current = await self.session.get(TargetVersionRecord, current.parent_version_id)
        if not records or records[-1].parent_version_id is not None:
            raise VersionPathError("target version ancestry is incomplete")
        return tuple(records)

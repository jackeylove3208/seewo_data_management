from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mappings import SnapshotEntityEmbedding
from app.schemas.canonical_entities import EntityType, SourceRole


class SnapshotEmbeddingRepository:
    """Tenant- and role-scoped persistence for immutable snapshot embeddings."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def existing_entity_ids(
        self,
        entity_ids: Sequence[UUID],
        *,
        tenant_id: str,
        snapshot_id: UUID,
        source_role: SourceRole,
        entity_type: EntityType,
        provider: str,
        model: str,
        representation_version: str,
    ) -> set[UUID]:
        if not entity_ids:
            return set()
        return set(
            await self.session.scalars(
                select(SnapshotEntityEmbedding.entity_id).where(
                    SnapshotEntityEmbedding.entity_id.in_(entity_ids),
                    SnapshotEntityEmbedding.tenant_id == tenant_id,
                    SnapshotEntityEmbedding.snapshot_id == snapshot_id,
                    SnapshotEntityEmbedding.source_role == source_role.value,
                    SnapshotEntityEmbedding.entity_type == entity_type.value,
                    SnapshotEntityEmbedding.provider == provider,
                    SnapshotEntityEmbedding.model == model,
                    SnapshotEntityEmbedding.representation_version == representation_version,
                )
            )
        )

    def opposite_partition(
        self,
        *,
        tenant_id: str,
        snapshot_id: UUID,
        source_role: SourceRole,
        entity_type: EntityType,
        provider: str,
        model: str,
        representation_version: str,
    ) -> Select[tuple[SnapshotEntityEmbedding]]:
        return select(SnapshotEntityEmbedding).where(
            SnapshotEntityEmbedding.tenant_id == tenant_id,
            SnapshotEntityEmbedding.snapshot_id == snapshot_id,
            SnapshotEntityEmbedding.source_role == source_role.value,
            SnapshotEntityEmbedding.entity_type == entity_type.value,
            SnapshotEntityEmbedding.provider == provider,
            SnapshotEntityEmbedding.model == model,
            SnapshotEntityEmbedding.representation_version == representation_version,
        )

    def add_all(self, records: Sequence[SnapshotEntityEmbedding]) -> None:
        self.session.add_all(records)

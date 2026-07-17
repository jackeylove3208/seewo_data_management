import math
from collections.abc import Iterator, Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import EmbeddingBatch, EmbeddingProvider
from app.matching.blocking import block_key
from app.models.mappings import TargetEntityEmbedding
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import BlockKey, Candidate, NormalizedRecord

REPRESENTATION_VERSION = "entity-representation-v1"
POSTGRES_VECTOR_DIMENSIONS = 1536


class VectorIndex:
    def __init__(
        self,
        session: AsyncSession,
        provider: EmbeddingProvider,
        *,
        representation_version: str = REPRESENTATION_VERSION,
        batch_size: int = 500,
        sqlite_scan_limit: int = 10_000,
    ) -> None:
        if batch_size < 1 or sqlite_scan_limit < 1:
            raise ValueError("batch_size and sqlite_scan_limit must be at least 1")
        if not 1 <= len(representation_version) <= 64:
            raise ValueError("representation_version must contain 1 to 64 characters")
        if not 1 <= len(provider.provider_name) <= 128:
            raise ValueError("provider_name must contain 1 to 128 characters")
        if not 1 <= len(provider.model) <= 255:
            raise ValueError("provider model must contain 1 to 255 characters")
        dialect = session.bind.dialect.name if session.bind is not None else ""
        if dialect == "postgresql" and provider.dimensions != POSTGRES_VECTOR_DIMENSIONS:
            raise ValueError(
                f"PostgreSQL vector storage requires {POSTGRES_VECTOR_DIMENSIONS} dimensions"
            )
        self.session = session
        self.provider = provider
        self.representation_version = representation_version
        self.batch_size = batch_size
        self.sqlite_scan_limit = sqlite_scan_limit

    async def upsert_targets(self, targets: Sequence[NormalizedRecord]) -> int:
        if not targets:
            return 0
        target_ids = [target.entity_id for target in targets]
        existing: set[UUID] = set()
        for entity_ids in _chunks(target_ids, self.batch_size):
            existing.update(
                await self.session.scalars(
                    select(TargetEntityEmbedding.entity_id).where(
                        TargetEntityEmbedding.entity_id.in_(entity_ids),
                        TargetEntityEmbedding.provider == self.provider.provider_name,
                        TargetEntityEmbedding.model == self.provider.model,
                        TargetEntityEmbedding.representation_version == self.representation_version,
                    )
                )
            )
        missing = [target for target in targets if target.entity_id not in existing]
        if not missing:
            return 0

        for target_batch in _chunks(missing, self.batch_size):
            texts = [representation(target) for target in target_batch]
            batch = await self.provider.embed(texts)
            _validate_batch(batch, self.provider, len(target_batch))
            self.session.add_all(
                TargetEntityEmbedding(
                    entity_id=target.entity_id,
                    snapshot_id=target.snapshot_id,
                    tenant_id=target.tenant_id,
                    entity_type=target.entity_type.value,
                    campus_id=_target_block.campus_id,
                    grade=_target_block.grade,
                    source_id=target.source_id,
                    normalized_values=target.values,
                    parent_mapping_id=target.parent_mapping_id,
                    block_key=_target_block.model_dump(mode="json"),
                    provider=batch.provider,
                    model=batch.model,
                    dimensions=self.provider.dimensions,
                    representation_version=self.representation_version,
                    representation=text,
                    embedding=vector,
                )
                for target, text, vector in zip(target_batch, texts, batch.vectors, strict=True)
                for _target_block in (block_key(target),)
            )
            await self.session.flush()
        return len(missing)

    async def search(
        self,
        query: str,
        block: BlockKey,
        *,
        target_snapshot_id: UUID,
        top_k: int = 20,
    ) -> list[Candidate]:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        batch = await self.provider.embed([query])
        _validate_batch(batch, self.provider, 1)
        query_vector = batch.vectors[0]
        statement = select(TargetEntityEmbedding).where(
            TargetEntityEmbedding.provider == self.provider.provider_name,
            TargetEntityEmbedding.model == self.provider.model,
            TargetEntityEmbedding.representation_version == self.representation_version,
            TargetEntityEmbedding.snapshot_id == target_snapshot_id,
            TargetEntityEmbedding.tenant_id == block.tenant_id,
            TargetEntityEmbedding.entity_type == block.entity_type.value,
            TargetEntityEmbedding.campus_id == block.campus_id,
            TargetEntityEmbedding.grade == block.grade,
            TargetEntityEmbedding.parent_mapping_id == block.parent_mapping_id,
        )
        dialect = self.session.bind.dialect.name if self.session.bind is not None else ""
        if dialect == "postgresql":
            await self.session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            distance = cast(Any, TargetEntityEmbedding.embedding).cosine_distance(query_vector)
            statement = statement.order_by(distance, TargetEntityEmbedding.entity_id).limit(top_k)
        elif dialect == "sqlite":
            statement = statement.limit(self.sqlite_scan_limit + 1)
        rows = list(await self.session.scalars(statement))
        if dialect == "sqlite" and len(rows) > self.sqlite_scan_limit:
            raise RuntimeError(
                "SQLite vector scan limit exceeded; use PostgreSQL/pgvector for this block"
            )
        ranked = sorted(
            (
                Candidate(
                    entity=_record(row),
                    block_key=block,
                    vector_score=_cosine_similarity(query_vector, row.embedding),
                )
                for row in rows
            ),
            key=lambda candidate: (-(candidate.vector_score or 0), str(candidate.entity_id)),
        )
        return ranked[:top_k]


def representation(record: NormalizedRecord) -> str:
    fields = (
        record.entity_type.value,
        record.values.get("display_name") or record.values.get("name"),
        record.values.get("organization_path"),
        record.values.get("grade"),
        record.values.get("subject_hint"),
    )
    return " | ".join(value for value in fields if value)


def _validate_batch(
    batch: EmbeddingBatch,
    provider: EmbeddingProvider,
    expected_count: int,
) -> None:
    if batch.provider != provider.provider_name or batch.model != provider.model:
        raise ValueError("embedding provider metadata does not match configured provider")
    if len(batch.vectors) != expected_count:
        raise ValueError("embedding provider returned an unexpected vector count")
    if any(len(vector) != provider.dimensions for vector in batch.vectors):
        raise ValueError("embedding provider returned an unexpected vector dimension")


def _record(row: TargetEntityEmbedding) -> NormalizedRecord:
    return NormalizedRecord(
        entity_id=row.entity_id,
        snapshot_id=row.snapshot_id,
        tenant_id=row.tenant_id,
        entity_type=EntityType(row.entity_type),
        source_id=row.source_id,
        values=row.normalized_values,
        parent_mapping_id=row.parent_mapping_id,
        rule_version="normalization-v1",
    )


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0
    similarity = sum(a * b for a, b in zip(left, right, strict=True)) / denominator
    return round(max(0.0, min(1.0, similarity)), 6)


def _chunks[T](values: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]

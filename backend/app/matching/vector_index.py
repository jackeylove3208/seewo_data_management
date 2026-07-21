import hashlib
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import EmbeddingBatch, EmbeddingProvider
from app.ai.tokenization import TaskTokenizationContext
from app.matching.blocking import block_key
from app.models.mappings import SnapshotEntityEmbedding
from app.repositories.embeddings import SnapshotEmbeddingRepository
from app.schemas.canonical_entities import EntityType, SourceRole
from app.schemas.matching import BlockKey, Candidate, NormalizedRecord

REPRESENTATION_VERSION = "entity-representation-v2"
POSTGRES_VECTOR_DIMENSIONS = 1536
DEFAULT_TOP_K = 3

PERSON_TYPES = frozenset({EntityType.TEACHER, EntityType.STUDENT})
REPRESENTATION_FIELDS: dict[EntityType, tuple[str, ...]] = {
    EntityType.ORGANIZATION_UNIT: ("display_name", "organization_path", "campus_id"),
    EntityType.CLASS: (
        "display_name",
        "class_name",
        "grade",
        "school_year",
        "class_number",
        "organization_path",
    ),
    EntityType.TEACHER: (
        "display_name",
        "employee_number",
        "phone",
        "email",
        "subject_hint",
        "organization_path",
    ),
    EntityType.STUDENT: (
        "display_name",
        "student_number",
        "phone",
        "email",
        "grade",
        "class_name",
    ),
    EntityType.MEMBERSHIP: ("member_source_id", "container_source_id", "role"),
}
PROTECTED_FIELDS = frozenset(
    {
        "source_id",
        "display_name",
        "name",
        "phone",
        "email",
        "employee_number",
        "student_number",
        "member_source_id",
        "container_source_id",
        "parent_source_id",
    }
)
LOCAL_SIMILARITY_FIELDS: dict[EntityType, tuple[str, ...]] = {
    EntityType.ORGANIZATION_UNIT: ("display_name", "organization_path"),
    EntityType.CLASS: ("display_name", "grade", "school_year", "class_number"),
    EntityType.TEACHER: ("display_name", "employee_number", "phone", "email"),
    EntityType.STUDENT: ("display_name", "student_number", "phone", "email"),
    EntityType.MEMBERSHIP: ("member_source_id", "container_source_id", "role"),
}


@dataclass(frozen=True)
class CandidateEdge:
    focal_entity_id: UUID
    focal_role: SourceRole
    candidate_entity_id: UUID
    candidate_role: SourceRole
    rank: int
    source_entity_id: UUID
    target_entity_id: UUID
    vector_score: float
    directions: tuple[Literal["source_to_target", "target_to_source"], ...]
    representation_version: str
    provider: str
    model: str


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
        self.repository = SnapshotEmbeddingRepository(session)

    async def upsert_snapshot(
        self,
        records: Sequence[NormalizedRecord],
        source_role: SourceRole,
        tokenization_context: TaskTokenizationContext | None = None,
    ) -> int:
        if not records:
            return 0
        self._require_tokenization_context(tokenization_context)
        partitions: dict[tuple[str, UUID, EntityType], list[NormalizedRecord]] = {}
        for record in records:
            partitions.setdefault(
                (record.tenant_id, record.snapshot_id, record.entity_type), []
            ).append(record)
        missing: list[NormalizedRecord] = []
        for (tenant_id, snapshot_id, entity_type), partition_records in partitions.items():
            existing: set[UUID] = set()
            for entity_ids in _chunks(
                [record.entity_id for record in partition_records], self.batch_size
            ):
                existing.update(
                    await self.repository.existing_entity_ids(
                        entity_ids,
                        tenant_id=tenant_id,
                        snapshot_id=snapshot_id,
                        source_role=source_role,
                        entity_type=entity_type,
                        provider=self.provider.provider_name,
                        model=self.provider.model,
                        representation_version=self.representation_version,
                    )
                )
            missing.extend(
                record for record in partition_records if record.entity_id not in existing
            )
        for record_batch in _chunks(missing, self.batch_size):
            texts = [
                representation(record, tokenization_context=tokenization_context)
                for record in record_batch
            ]
            batch = await self.provider.embed(texts)
            _validate_batch(batch, self.provider, len(record_batch))
            self.repository.add_all(
                [
                    SnapshotEntityEmbedding(
                        entity_id=record.entity_id,
                        snapshot_id=record.snapshot_id,
                        tenant_id=record.tenant_id,
                        source_role=source_role.value,
                        entity_type=record.entity_type.value,
                        campus_id=record_block.campus_id,
                        grade=record_block.grade,
                        source_id=record.source_id,
                        normalized_values=record.values,
                        parent_mapping_id=record.parent_mapping_id,
                        block_key=record_block.model_dump(mode="json"),
                        provider=batch.provider,
                        model=batch.model,
                        dimensions=self.provider.dimensions,
                        representation_version=self.representation_version,
                        representation=representation_text,
                        embedding=vector,
                    )
                    for record, representation_text, vector in zip(
                        record_batch, texts, batch.vectors, strict=True
                    )
                    for record_block in (block_key(record),)
                ]
            )
            await self.session.flush()
        return len(missing)

    async def upsert_targets(self, targets: Sequence[NormalizedRecord]) -> int:
        return await self.upsert_snapshot(targets, SourceRole.TARGET)

    async def search_opposite(
        self,
        record: NormalizedRecord,
        source_role: SourceRole,
        *,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
        top_k: int = DEFAULT_TOP_K,
        relaxed: bool = False,
        tokenization_context: TaskTokenizationContext | None = None,
    ) -> list[Candidate]:
        self._require_tokenization_context(tokenization_context)
        opposite_role = (
            SourceRole.TARGET
            if source_role is SourceRole.AUTHORITATIVE
            else SourceRole.AUTHORITATIVE
        )
        opposite_snapshot_id = (
            target_snapshot_id if opposite_role is SourceRole.TARGET else source_snapshot_id
        )
        return await self._search(
            representation(record, tokenization_context=tokenization_context),
            block_key(record),
            snapshot_id=opposite_snapshot_id,
            source_role=opposite_role,
            top_k=top_k,
            relaxed=relaxed,
        )

    async def bidirectional_edges(
        self,
        authoritative_records: Sequence[NormalizedRecord],
        target_records: Sequence[NormalizedRecord],
        *,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
        top_k: int = DEFAULT_TOP_K,
        tokenization_context: TaskTokenizationContext | None = None,
    ) -> list[CandidateEdge]:
        edges: dict[tuple[SourceRole, UUID, UUID], CandidateEdge] = {}
        for direction, role, records in (
            ("source_to_target", SourceRole.AUTHORITATIVE, authoritative_records),
            ("target_to_source", SourceRole.TARGET, target_records),
        ):
            for record in records:
                candidates = await self.search_opposite(
                    record,
                    role,
                    source_snapshot_id=source_snapshot_id,
                    target_snapshot_id=target_snapshot_id,
                    top_k=top_k,
                    tokenization_context=tokenization_context,
                )
                if not candidates:
                    candidates = await self.search_opposite(
                        record,
                        role,
                        source_snapshot_id=source_snapshot_id,
                        target_snapshot_id=target_snapshot_id,
                        top_k=top_k,
                        relaxed=True,
                        tokenization_context=tokenization_context,
                    )
                candidate_role = (
                    SourceRole.TARGET
                    if role is SourceRole.AUTHORITATIVE
                    else SourceRole.AUTHORITATIVE
                )
                for rank, candidate in enumerate(candidates, start=1):
                    source_id, target_id = (
                        (record.entity_id, candidate.entity_id)
                        if role is SourceRole.AUTHORITATIVE
                        else (candidate.entity_id, record.entity_id)
                    )
                    key = (role, record.entity_id, candidate.entity_id)
                    score = candidate.vector_score or 0.0
                    edges[key] = CandidateEdge(
                        focal_entity_id=record.entity_id,
                        focal_role=role,
                        candidate_entity_id=candidate.entity_id,
                        candidate_role=candidate_role,
                        rank=rank,
                        source_entity_id=source_id,
                        target_entity_id=target_id,
                        vector_score=score,
                        directions=(cast(Any, direction),),
                        representation_version=self.representation_version,
                        provider=self.provider.provider_name,
                        model=self.provider.model,
                    )
        return sorted(
            edges.values(),
            key=lambda edge: (
                edge.focal_role.value,
                str(edge.focal_entity_id),
                edge.rank,
                str(edge.candidate_entity_id),
            ),
        )

    async def search(
        self,
        query: str,
        block: BlockKey,
        *,
        target_snapshot_id: UUID,
        top_k: int = 20,
    ) -> list[Candidate]:
        return await self._search(
            query,
            block,
            snapshot_id=target_snapshot_id,
            source_role=SourceRole.TARGET,
            top_k=top_k,
            relaxed=False,
        )

    async def _search(
        self,
        query: str,
        block: BlockKey,
        *,
        snapshot_id: UUID,
        source_role: SourceRole,
        top_k: int,
        relaxed: bool,
    ) -> list[Candidate]:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        batch = await self.provider.embed([query])
        _validate_batch(batch, self.provider, 1)
        query_vector = batch.vectors[0]
        statement = self.repository.opposite_partition(
            tenant_id=block.tenant_id,
            snapshot_id=snapshot_id,
            source_role=source_role,
            entity_type=block.entity_type,
            provider=self.provider.provider_name,
            model=self.provider.model,
            representation_version=self.representation_version,
        )
        if not relaxed:
            statement = statement.where(
                SnapshotEntityEmbedding.campus_id == block.campus_id,
                SnapshotEntityEmbedding.grade == block.grade,
                SnapshotEntityEmbedding.parent_mapping_id == block.parent_mapping_id,
            )
        dialect = self.session.bind.dialect.name if self.session.bind is not None else ""
        if dialect == "postgresql":
            await self.session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            distance = cast(Any, SnapshotEntityEmbedding.embedding).cosine_distance(query_vector)
            statement = statement.order_by(distance, SnapshotEntityEmbedding.entity_id).limit(top_k)
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

    def _require_tokenization_context(
        self, tokenization_context: TaskTokenizationContext | None
    ) -> None:
        if getattr(self.provider, "requires_tokenization", False) and tokenization_context is None:
            raise ValueError("external embedding requests require task-scoped tokenization")


def representation(
    record: NormalizedRecord,
    *,
    tokenization_context: TaskTokenizationContext | None = None,
) -> str:
    values: list[str] = [f"entity_type={record.entity_type.value}"]
    source_id = _protected_value(
        "source_id", record.source_id, record.entity_type, tokenization_context
    )
    values.append(f"source_id={source_id}")
    for field in REPRESENTATION_FIELDS[record.entity_type]:
        value = record.values.get(field)
        if not value:
            continue
        if field in PROTECTED_FIELDS or (
            field == "display_name" and record.entity_type in PERSON_TYPES
        ):
            token_field = "name" if field == "display_name" else field
            value = _protected_value(token_field, value, record.entity_type, tokenization_context)
        values.append(f"{field}={value}")
    return " | ".join(values)


def local_similarity_features(
    source: NormalizedRecord,
    target: NormalizedRecord,
) -> dict[str, float]:
    if source.entity_type is not target.entity_type:
        return {}
    return {
        field: float(
            bool(source.values.get(field)) and source.values.get(field) == target.values.get(field)
        )
        for field in LOCAL_SIMILARITY_FIELDS[source.entity_type]
        if source.values.get(field) is not None or target.values.get(field) is not None
    }


def _protected_value(
    field: str,
    value: str,
    entity_type: EntityType,
    tokenization_context: TaskTokenizationContext | None,
) -> str:
    if tokenization_context is not None:
        return tokenization_context.tokenize_value(
            field,
            value,
            entity_type=entity_type.value,
        )
    digest = hashlib.sha256(f"{entity_type.value}:{field}:{value}".encode()).hexdigest()[:12]
    return f"REDACTED_{digest.upper()}"


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


def _record(row: SnapshotEntityEmbedding) -> NormalizedRecord:
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

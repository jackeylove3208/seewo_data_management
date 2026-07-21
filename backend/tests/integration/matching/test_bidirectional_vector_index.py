import re
from uuid import UUID, uuid4

import pytest

from app.ai.providers.base import EmbeddingBatch
from app.ai.tokenization import TaskTokenizationContext
from app.matching.vector_index import VectorIndex
from app.models.snapshots import CanonicalEntityRecord
from app.repositories.embeddings import SnapshotEmbeddingRepository
from app.schemas.canonical_entities import EntityType, SourceRole
from app.schemas.matching import NormalizedRecord
from tests.fixtures.organization_factory import create_hierarchy_pair


class TokenAwareEmbeddingProvider:
    dimensions = 3
    provider_name = "fake-enterprise"
    model = "fake-embedding-v2"
    requires_tokenization = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts):
        values = tuple(texts)
        self.calls.append(values)
        vectors: list[list[float]] = []
        for value in values:
            token = re.search(r"PHONE_[A-F0-9]{12}", value)
            seed = sum(ord(character) for character in (token.group(0) if token else value))
            vectors.append(
                [
                    float((seed % 17) + 1),
                    float((seed % 29) + 1),
                    float((seed % 43) + 1),
                ]
            )
        return EmbeddingBatch(
            vectors=vectors,
            provider=self.provider_name,
            model=self.model,
        )


def student(
    source_id: str,
    snapshot_id: UUID,
    *,
    tenant_id: str = "school-1",
    name: str,
    phone: str,
    parent_mapping_id: UUID | None = None,
) -> NormalizedRecord:
    return NormalizedRecord(
        entity_id=uuid4(),
        snapshot_id=snapshot_id,
        tenant_id=tenant_id,
        entity_type=EntityType.STUDENT,
        source_id=source_id,
        values={
            "name": name,
            "display_name": name,
            "phone": phone,
            "email": None,
            "grade": "高一",
            "class_name": "高一1班",
        },
        parent_mapping_id=parent_mapping_id,
        rule_version="normalization-v1",
    )


async def persist(session, records: list[NormalizedRecord]) -> None:
    session.add_all(
        CanonicalEntityRecord(
            id=record.entity_id,
            snapshot_id=record.snapshot_id,
            entity_type=record.entity_type.value,
            source_id=record.source_id,
            raw_row_number=index + 100,
            canonical_payload=record.values,
            raw_payload=record.values,
        )
        for index, record in enumerate(records)
    )
    await session.flush()


def token_context(task_id: UUID) -> TaskTokenizationContext:
    return TaskTokenizationContext(
        secret="test-secret-at-least-16-characters",
        tenant_id="school-1",
        task_id=task_id,
    )


@pytest.mark.asyncio
async def test_external_embedding_requires_task_scoped_tokenization(session) -> None:
    pair = await create_hierarchy_pair(session)
    provider = TokenAwareEmbeddingProvider()
    index = VectorIndex(session, provider)
    source = student(
        "source-sensitive",
        pair.source_snapshot_id,
        name="测试姓名",
        phone="13800000000",
    )
    await persist(session, [source])

    with pytest.raises(ValueError, match="task-scoped tokenization"):
        await index.upsert_snapshot([source], SourceRole.AUTHORITATIVE)

    assert provider.calls == []


@pytest.mark.asyncio
async def test_indexes_both_roles_idempotently_and_queries_only_opposite_role(session) -> None:
    pair = await create_hierarchy_pair(session)
    provider = TokenAwareEmbeddingProvider()
    index = VectorIndex(session, provider)
    parent = uuid4()
    source = student(
        "source-s1",
        pair.source_snapshot_id,
        name="张三",
        phone="13800000000",
        parent_mapping_id=parent,
    )
    target = student(
        "target-s1",
        pair.target_snapshot_id,
        name="张三",
        phone="13800000000",
        parent_mapping_id=parent,
    )
    wrong_role = student(
        "source-shadow",
        pair.target_snapshot_id,
        name="张三",
        phone="13800000000",
        parent_mapping_id=parent,
    )
    await persist(session, [source, target, wrong_role])
    context = token_context(pair.task_id)

    assert await index.upsert_snapshot([source], SourceRole.AUTHORITATIVE, context) == 1
    assert await index.upsert_snapshot([target], SourceRole.TARGET, context) == 1
    assert await index.upsert_snapshot([wrong_role], SourceRole.AUTHORITATIVE, context) == 1
    assert await index.upsert_snapshot([source], SourceRole.AUTHORITATIVE, context) == 0

    results = await index.search_opposite(
        source,
        SourceRole.AUTHORITATIVE,
        source_snapshot_id=pair.source_snapshot_id,
        target_snapshot_id=pair.target_snapshot_id,
        top_k=3,
        tokenization_context=context,
    )

    assert [candidate.entity_id for candidate in results] == [target.entity_id]
    assert all(
        raw not in "\n".join(text for call in provider.calls for text in call)
        for raw in ("张三", "13800000000", "source-s1", "target-s1")
    )

    repository = SnapshotEmbeddingRepository(session)
    assert await repository.existing_entity_ids(
        [source.entity_id],
        tenant_id="school-1",
        snapshot_id=pair.source_snapshot_id,
        source_role=SourceRole.AUTHORITATIVE,
        entity_type=EntityType.STUDENT,
        provider=provider.provider_name,
        model=provider.model,
        representation_version="entity-representation-v2",
    ) == {source.entity_id}
    assert (
        await repository.existing_entity_ids(
            [source.entity_id],
            tenant_id="school-2",
            snapshot_id=pair.source_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            entity_type=EntityType.STUDENT,
            provider=provider.provider_name,
            model=provider.model,
            representation_version="entity-representation-v2",
        )
        == set()
    )
    assert (
        await repository.existing_entity_ids(
            [source.entity_id],
            tenant_id="school-1",
            snapshot_id=pair.target_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            entity_type=EntityType.STUDENT,
            provider=provider.provider_name,
            model=provider.model,
            representation_version="entity-representation-v2",
        )
        == set()
    )
    assert (
        await repository.existing_entity_ids(
            [source.entity_id],
            tenant_id="school-1",
            snapshot_id=pair.source_snapshot_id,
            source_role=SourceRole.TARGET,
            entity_type=EntityType.STUDENT,
            provider=provider.provider_name,
            model=provider.model,
            representation_version="entity-representation-v2",
        )
        == set()
    )
    assert (
        await repository.existing_entity_ids(
            [source.entity_id],
            tenant_id="school-1",
            snapshot_id=pair.source_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            entity_type=EntityType.TEACHER,
            provider=provider.provider_name,
            model=provider.model,
            representation_version="entity-representation-v2",
        )
        == set()
    )
    assert (
        await repository.existing_entity_ids(
            [source.entity_id],
            tenant_id="school-1",
            snapshot_id=pair.source_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            entity_type=EntityType.STUDENT,
            provider="other-provider",
            model=provider.model,
            representation_version="entity-representation-v2",
        )
        == set()
    )
    assert (
        await repository.existing_entity_ids(
            [source.entity_id],
            tenant_id="school-1",
            snapshot_id=pair.source_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            entity_type=EntityType.STUDENT,
            provider=provider.provider_name,
            model="other-model",
            representation_version="entity-representation-v2",
        )
        == set()
    )
    assert (
        await repository.existing_entity_ids(
            [source.entity_id],
            tenant_id="school-1",
            snapshot_id=pair.source_snapshot_id,
            source_role=SourceRole.AUTHORITATIVE,
            entity_type=EntityType.STUDENT,
            provider=provider.provider_name,
            model=provider.model,
            representation_version="other-version",
        )
        == set()
    )


@pytest.mark.asyncio
async def test_relaxed_reverse_top_three_recovers_target_entity_and_deduplicates_edges(
    session,
) -> None:
    pair = await create_hierarchy_pair(session)
    provider = TokenAwareEmbeddingProvider()
    index = VectorIndex(session, provider)
    source_parent = uuid4()
    target_parent = uuid4()
    source = student(
        "source-s1",
        pair.source_snapshot_id,
        name="王小明",
        phone="13900000000",
        parent_mapping_id=source_parent,
    )
    targets = [
        student(
            f"target-s{number}",
            pair.target_snapshot_id,
            name="王小明" if number == 1 else f"候选{number}",
            phone="13900000000" if number == 1 else f"1370000000{number}",
            parent_mapping_id=target_parent,
        )
        for number in range(1, 6)
    ]
    await persist(session, [source, *targets])
    context = token_context(pair.task_id)
    await index.upsert_snapshot([source], SourceRole.AUTHORITATIVE, context)
    await index.upsert_snapshot(targets, SourceRole.TARGET, context)

    strict = await index.search_opposite(
        source,
        SourceRole.AUTHORITATIVE,
        source_snapshot_id=pair.source_snapshot_id,
        target_snapshot_id=pair.target_snapshot_id,
        top_k=3,
        relaxed=False,
        tokenization_context=context,
    )
    relaxed = await index.search_opposite(
        source,
        SourceRole.AUTHORITATIVE,
        source_snapshot_id=pair.source_snapshot_id,
        target_snapshot_id=pair.target_snapshot_id,
        top_k=3,
        relaxed=True,
        tokenization_context=context,
    )
    edges = await index.bidirectional_edges(
        [source],
        targets,
        source_snapshot_id=pair.source_snapshot_id,
        target_snapshot_id=pair.target_snapshot_id,
        top_k=3,
        tokenization_context=context,
    )

    assert strict == []
    assert len(relaxed) == 3
    assert relaxed[0].entity_id == targets[0].entity_id
    assert any(
        edge.focal_entity_id == source.entity_id
        and edge.focal_role is SourceRole.AUTHORITATIVE
        and edge.candidate_entity_id == targets[0].entity_id
        and edge.candidate_role is SourceRole.TARGET
        for edge in edges
    )
    assert any(
        edge.focal_entity_id == targets[0].entity_id
        and edge.focal_role is SourceRole.TARGET
        and edge.candidate_entity_id == source.entity_id
        and edge.candidate_role is SourceRole.AUTHORITATIVE
        for edge in edges
    )
    assert len(
        {(edge.focal_role, edge.focal_entity_id, edge.candidate_entity_id) for edge in edges}
    ) == len(edges)
    assert len(edges) <= 3 * (1 + len(targets))

    by_focal = {}
    for edge in edges:
        by_focal.setdefault((edge.focal_role, edge.focal_entity_id), []).append(edge)
    assert all(len(focal_edges) <= 3 for focal_edges in by_focal.values())
    assert all(edge.focal_role is not edge.candidate_role for edge in edges)
    assert all(
        [edge.rank for edge in focal_edges] == list(range(1, len(focal_edges) + 1))
        for focal_edges in by_focal.values()
    )
    assert all(
        edge.source_entity_id in {source.entity_id}
        and edge.target_entity_id in {target.entity_id for target in targets}
        for edge in edges
        if edge.focal_role is SourceRole.AUTHORITATIVE
    )
    assert all(
        edge.source_entity_id in {source.entity_id}
        and edge.target_entity_id in {target.entity_id for target in targets}
        for edge in edges
        if edge.focal_role is SourceRole.TARGET
    )


@pytest.mark.asyncio
async def test_candidate_generation_is_bounded_for_large_snapshot_pair(session) -> None:
    pair = await create_hierarchy_pair(session)
    provider = TokenAwareEmbeddingProvider()
    index = VectorIndex(session, provider, batch_size=16, sqlite_scan_limit=100)
    sources = [
        student(
            f"source-{number}",
            pair.source_snapshot_id,
            name=f"学生{number}",
            phone=f"1360000{number:04d}",
        )
        for number in range(40)
    ]
    targets = [
        student(
            f"target-{number}",
            pair.target_snapshot_id,
            name=f"学生{number}",
            phone=f"1360000{number:04d}",
        )
        for number in range(50)
    ]
    await persist(session, [*sources, *targets])
    context = token_context(pair.task_id)
    await index.upsert_snapshot(sources, SourceRole.AUTHORITATIVE, context)
    await index.upsert_snapshot(targets, SourceRole.TARGET, context)

    edges = await index.bidirectional_edges(
        sources,
        targets,
        source_snapshot_id=pair.source_snapshot_id,
        target_snapshot_id=pair.target_snapshot_id,
        top_k=3,
        tokenization_context=context,
    )

    assert len(edges) <= 3 * (len(sources) + len(targets))
    assert len(edges) < len(sources) * len(targets)
    assert max(len(call) for call in provider.calls) <= 16

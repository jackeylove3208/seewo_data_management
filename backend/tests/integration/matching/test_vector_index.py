from uuid import uuid4

import pytest

from app.ai.providers.base import EmbeddingBatch
from app.matching.blocking import block_key
from app.matching.vector_index import VectorIndex
from app.models.snapshots import CanonicalEntityRecord
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import NormalizedRecord
from tests.fixtures.organization_factory import create_hierarchy_pair


class FakeEmbeddingProvider:
    dimensions = 3
    provider_name = "fake"
    model = "fake-embedding-v1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts):
        values = tuple(texts)
        self.calls.append(values)
        vectors = []
        for value in values:
            if "张三" in value:
                vectors.append([1.0, 0.0, 0.0])
            elif "李四" in value:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return EmbeddingBatch(
            vectors=vectors,
            provider="fake",
            model="fake-embedding-v1",
        )


def target(name: str, parent_mapping_id, snapshot_id=None) -> NormalizedRecord:
    return NormalizedRecord(
        entity_id=uuid4(),
        snapshot_id=snapshot_id or uuid4(),
        tenant_id="school-1",
        entity_type=EntityType.TEACHER,
        source_id=f"target-{name}",
        values={"name": name, "display_name": name, "subject_hint": "语文"},
        parent_mapping_id=parent_mapping_id,
        rule_version="normalization-v1",
    )


async def persist_targets(session, targets: list[NormalizedRecord]) -> None:
    session.add_all(
        [
            CanonicalEntityRecord(
                id=record.entity_id,
                snapshot_id=record.snapshot_id,
                entity_type=record.entity_type.value,
                source_id=record.source_id,
                raw_row_number=index + 100,
                canonical_payload=record.values,
                raw_payload=record.values,
            )
            for index, record in enumerate(targets)
        ]
    )
    await session.flush()


@pytest.mark.asyncio
async def test_vector_search_is_top_k_blocked_and_cached(session) -> None:
    provider = FakeEmbeddingProvider()
    parent = uuid4()
    pair = await create_hierarchy_pair(session)
    snapshot_id = pair.target_snapshot_id
    compatible = [
        target("张三", parent, snapshot_id),
        target("李四", parent, snapshot_id),
        target("王五", parent, snapshot_id),
    ]
    incompatible = target("张三", uuid4(), snapshot_id)
    index = VectorIndex(session, provider)

    await persist_targets(session, [*compatible, incompatible])
    await index.upsert_targets([*compatible, incompatible])
    await session.flush()
    await index.upsert_targets([*compatible, incompatible])
    results = await index.search(
        "张三 语文",
        block_key(compatible[0]),
        target_snapshot_id=snapshot_id,
        top_k=2,
    )

    assert len(results) == 2
    assert results[0].entity_id == compatible[0].entity_id
    assert all(result.block_key == block_key(compatible[0]) for result in results)
    assert all(result.vector_score is not None for result in results)
    assert len(provider.calls[0]) == 4
    assert len(provider.calls) == 2  # one target batch plus one query; second upsert was cached


@pytest.mark.asyncio
async def test_vector_search_never_returns_another_snapshot(session) -> None:
    provider = FakeEmbeddingProvider()
    parent = uuid4()
    current_pair = await create_hierarchy_pair(session)
    old_pair = await create_hierarchy_pair(session)
    current_snapshot_id = current_pair.target_snapshot_id
    current = target("张三", parent, current_snapshot_id)
    old = target("张三", parent, old_pair.target_snapshot_id)
    index = VectorIndex(session, provider)
    await persist_targets(session, [current, old])
    await index.upsert_targets([current, old])

    results = await index.search(
        "张三",
        block_key(current),
        target_snapshot_id=current_snapshot_id,
        top_k=10,
    )

    assert [result.entity_id for result in results] == [current.entity_id]


@pytest.mark.asyncio
async def test_vector_upsert_chunks_database_and_provider_batches(session) -> None:
    provider = FakeEmbeddingProvider()
    parent = uuid4()
    pair = await create_hierarchy_pair(session)
    snapshot_id = pair.target_snapshot_id
    targets = [target(f"教师{index}", parent, snapshot_id) for index in range(205)]
    index = VectorIndex(session, provider, batch_size=50)

    await persist_targets(session, targets)
    assert await index.upsert_targets(targets) == 205
    target_calls = list(provider.calls)
    assert [len(call) for call in target_calls] == [50, 50, 50, 50, 5]

    assert await index.upsert_targets(targets) == 0
    assert provider.calls == target_calls


@pytest.mark.asyncio
async def test_sqlite_vector_search_has_explicit_development_limit(session) -> None:
    provider = FakeEmbeddingProvider()
    parent = uuid4()
    pair = await create_hierarchy_pair(session)
    snapshot_id = pair.target_snapshot_id
    targets = [target("张三", parent, snapshot_id), target("李四", parent, snapshot_id)]
    index = VectorIndex(session, provider, sqlite_scan_limit=1)
    await persist_targets(session, targets)
    await index.upsert_targets(targets)

    with pytest.raises(RuntimeError, match="SQLite vector scan limit"):
        await index.search(
            "张三",
            block_key(targets[0]),
            target_snapshot_id=snapshot_id,
        )

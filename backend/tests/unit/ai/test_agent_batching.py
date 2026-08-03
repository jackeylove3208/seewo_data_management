from uuid import uuid4

import pytest

from app.ai.agent_batching import partition_analysis_batches
from app.schemas.agent_ingestion import AgentEntityKind


def test_default_partition_limits_43_work_items_to_batches_of_ten() -> None:
    items = tuple(uuid4() for _ in range(43))

    batches = partition_analysis_batches(
        (AgentEntityKind.STUDENT, item) for item in items
    )

    assert [len(batch.work_item_ids) for batch in batches] == [10, 10, 10, 10, 3]


def test_partitions_11_work_items_into_deterministic_batches_of_at_most_10() -> None:
    items = tuple(uuid4() for _ in range(11))

    batches = partition_analysis_batches(
        ((AgentEntityKind.STUDENT, item) for item in items), max_items=10
    )

    assert [len(batch.work_item_ids) for batch in batches] == [10, 1]
    assert batches[0].work_item_ids == items[:10]
    assert batches[1].work_item_ids == items[10:]


def test_partitioning_never_mixes_entity_types() -> None:
    student = uuid4()
    teacher = uuid4()

    batches = partition_analysis_batches(
        ((AgentEntityKind.STUDENT, student), (AgentEntityKind.TEACHER, teacher)), max_items=10
    )

    assert [(batch.entity_kind, batch.work_item_ids) for batch in batches] == [
        (AgentEntityKind.STUDENT, (student,)),
        (AgentEntityKind.TEACHER, (teacher,)),
    ]


def test_partitioning_rejects_a_limit_above_ten() -> None:
    with pytest.raises(ValueError, match="between 1 and 10"):
        partition_analysis_batches((), max_items=11)

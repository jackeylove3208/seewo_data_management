from uuid import uuid4

from app.ai.agent_batching import partition_analysis_batches
from app.schemas.agent_ingestion import AgentEntityKind


def test_partitions_51_work_items_into_deterministic_batches_of_at_most_50() -> None:
    items = tuple(uuid4() for _ in range(51))

    batches = partition_analysis_batches(
        ((AgentEntityKind.STUDENT, item) for item in items), max_items=50
    )

    assert [len(batch.work_item_ids) for batch in batches] == [50, 1]
    assert batches[0].work_item_ids == items[:50]
    assert batches[1].work_item_ids == items[50:]


def test_partitioning_never_mixes_entity_types() -> None:
    student = uuid4()
    teacher = uuid4()

    batches = partition_analysis_batches(
        ((AgentEntityKind.STUDENT, student), (AgentEntityKind.TEACHER, teacher)), max_items=50
    )

    assert [(batch.entity_kind, batch.work_item_ids) for batch in batches] == [
        (AgentEntityKind.STUDENT, (student,)),
        (AgentEntityKind.TEACHER, (teacher,)),
    ]

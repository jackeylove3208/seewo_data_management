from uuid import uuid4

from app.matching.candidate_retriever import CandidateRetriever
from app.matching.exact_matcher import ExactMatcher
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import NormalizedRecord


def record(
    source_id: str,
    parent_mapping_id,
    *,
    employee_number: str | None = None,
) -> NormalizedRecord:
    return NormalizedRecord(
        entity_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        entity_type=EntityType.TEACHER,
        source_id=source_id,
        values={
            "name": source_id,
            "display_name": source_id,
            "employee_number": employee_number,
        },
        parent_mapping_id=parent_mapping_id,
        rule_version="normalization-v1",
    )


def test_candidate_retrieval_avoids_all_pairs_comparison() -> None:
    parents = [uuid4() for _ in range(50)]
    targets = [record(f"teacher-{index}", parents[index % 50]) for index in range(500)]
    sources = [record(f"teacher-{index}", parents[index % 50]) for index in range(500)]
    retriever = CandidateRetriever(targets)

    for source in sources:
        retriever.lexical(source, top_k=5)

    assert retriever.max_returned <= 5
    assert retriever.comparisons < len(sources) * len(targets)
    assert retriever.posting_visits < len(sources) * len(targets)


def test_exact_matching_builds_target_index_once() -> None:
    parent = uuid4()
    targets = [
        record(f"target-{index}", parent, employee_number=f"E{index:05d}") for index in range(500)
    ]
    sources = [
        record(f"source-{index}", parent, employee_number=f"X{index:05d}") for index in range(500)
    ]
    matcher = ExactMatcher()

    index = matcher.build_index(targets)
    for source in sources:
        matcher.match(source, index)

    assert index.indexed_records == len(targets)
    assert index.lookup_count == len(sources)
    assert index.lookup_count < len(sources) * len(targets)

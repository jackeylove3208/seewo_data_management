from uuid import uuid4

from app.matching.blocking import block_key
from app.matching.candidate_retriever import CandidateRetriever
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import NormalizedRecord


def record(
    source_id: str,
    name: str,
    *,
    tenant_id: str = "school-1",
    entity_type: EntityType = EntityType.TEACHER,
    parent_mapping_id=None,
    grade: str | None = None,
) -> NormalizedRecord:
    return NormalizedRecord(
        entity_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id=tenant_id,
        entity_type=entity_type,
        source_id=source_id,
        values={
            "display_name": name,
            "name": name,
            "organization_path": "本部/教务处",
            "grade": grade,
        },
        parent_mapping_id=parent_mapping_id,
        rule_version="normalization-v1",
    )


def test_teacher_candidates_stay_in_tenant_type_and_parent() -> None:
    parent = uuid4()
    source = record("source", "张三", parent_mapping_id=parent)
    compatible = [
        record(f"target-{index}", f"张三{index}", parent_mapping_id=parent) for index in range(8)
    ]
    wrong_tenant = record("other-tenant", "张三", tenant_id="school-2", parent_mapping_id=parent)
    wrong_parent = record("other-parent", "张三", parent_mapping_id=uuid4())
    wrong_type = record(
        "student",
        "张三",
        entity_type=EntityType.STUDENT,
        parent_mapping_id=parent,
    )
    retriever = CandidateRetriever([*compatible, wrong_tenant, wrong_parent, wrong_type])

    candidates = retriever.lexical(source, top_k=5)

    assert len(candidates) == 5
    assert all(candidate.block_key == block_key(source) for candidate in candidates)
    assert {candidate.entity_id for candidate in candidates} <= {
        target.entity_id for target in compatible
    }


def test_no_compatible_block_returns_empty() -> None:
    source = record("source", "张三", tenant_id="unknown")
    retriever = CandidateRetriever([record("target", "张三")])

    assert retriever.lexical(source, top_k=5) == []


def test_lexical_candidates_are_ranked_with_evidence() -> None:
    source = record("source", "张三")
    exact_name = record("exact", "张三")
    distant_name = record("distant", "李四")
    retriever = CandidateRetriever([distant_name, exact_name])

    candidates = retriever.lexical(source, top_k=2)

    assert candidates[0].entity_id == exact_name.entity_id
    assert candidates[0].lexical_score == 1
    assert candidates[0].vector_score is None
    assert retriever.max_returned == 2

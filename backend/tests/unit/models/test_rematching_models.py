from app.models.mappings import EntityMapping
from app.models.rematching import (
    EntityRematchCandidateEdgeRecord,
    EntityRematchJobRecord,
    EntityRematchWorkItemRecord,
)


def test_rematching_tables_and_mapping_supersession_are_declared() -> None:
    assert EntityRematchJobRecord.__tablename__ == "entity_rematch_jobs"
    assert EntityRematchWorkItemRecord.__tablename__ == "entity_rematch_work_items"
    assert EntityRematchCandidateEdgeRecord.__tablename__ == "entity_rematch_candidate_edges"
    assert "supersedes_mapping_id" in EntityMapping.__table__.columns


def test_rematching_models_declare_idempotency_and_candidate_uniqueness() -> None:
    job_constraints = {
        constraint.name for constraint in EntityRematchJobRecord.__table__.constraints
    }
    edge_constraints = {
        constraint.name for constraint in EntityRematchCandidateEdgeRecord.__table__.constraints
    }

    assert "uq_entity_rematch_job_idempotency" in job_constraints
    assert "uq_entity_rematch_candidate_edge" in edge_constraints

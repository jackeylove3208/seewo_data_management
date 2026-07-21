from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_entity_rematch_migration_is_reversible(tmp_path: Path) -> None:
    database_path = tmp_path / "entity-rematch-migration.db"
    url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert {
        "entity_rematch_jobs",
        "entity_rematch_work_items",
        "entity_rematch_candidate_edges",
    } <= set(inspector.get_table_names())
    assert "supersedes_mapping_id" in {
        column["name"] for column in inspector.get_columns("entity_mappings")
    }
    job_uniques = {
        constraint["name"] for constraint in inspector.get_unique_constraints("entity_rematch_jobs")
    }
    edge_uniques = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("entity_rematch_candidate_edges")
    }
    assert "uq_entity_rematch_job_idempotency" in job_uniques
    assert "uq_entity_rematch_candidate_edge" in edge_uniques

    command.downgrade(config, "0012_snapshot_entity_embeddings")
    inspector = inspect(engine)
    assert "entity_rematch_jobs" not in inspector.get_table_names()
    assert "supersedes_mapping_id" not in {
        column["name"] for column in inspector.get_columns("entity_mappings")
    }

    command.upgrade(config, "head")
    assert "entity_rematch_jobs" in inspect(engine).get_table_names()

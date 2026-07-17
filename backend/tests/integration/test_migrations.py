from io import StringIO
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_initial_migration_creates_ingestion_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    tables = set(inspect(create_engine(f"sqlite:///{database_path}")).get_table_names())
    assert {
        "alembic_version",
        "reconciliation_tasks",
        "source_files",
        "snapshots",
        "raw_snapshot_rows",
        "canonical_entities",
        "ingestion_issues",
        "entity_mappings",
        "target_entity_embeddings",
        "difference_items",
        "analysis_results",
    } <= tables


def test_postgresql_migration_guards_analysis_history_with_a_trigger() -> None:
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    config.set_main_option(
        "sqlalchemy.url",
        "postgresql+asyncpg://reconcile:reconcile@localhost:5432/reconcile",
    )

    command.upgrade(config, "head", sql=True)

    ddl = output.getvalue()
    assert "CREATE TRIGGER reject_analysis_results_mutation" in ddl
    assert "BEFORE UPDATE OR DELETE ON analysis_results" in ddl

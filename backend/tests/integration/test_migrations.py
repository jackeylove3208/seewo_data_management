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
        "workflow_stage_runs",
        "governance_proposals",
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


def test_workflow_stage_migration_downgrades_and_reapplies(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow-migration.db"
    url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")
    command.downgrade(config, "0006_analysis_immutability")
    assert "workflow_stage_runs" not in inspect(create_engine(url)).get_table_names()

    command.upgrade(config, "head")
    assert "workflow_stage_runs" in inspect(create_engine(url)).get_table_names()


def test_governance_proposal_migration_is_reversible_and_immutable(tmp_path: Path) -> None:
    database_path = tmp_path / "proposal-migration.db"
    url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")
    engine = create_engine(url)
    triggers = {
        row[0]
        for row in engine.connect().exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    }
    assert {
        "reject_governance_proposals_update",
        "reject_governance_proposals_delete",
    } <= triggers

    command.downgrade(config, "0008_analysis_gateway_requests")
    assert "governance_proposals" not in inspect(engine).get_table_names()

    command.upgrade(config, "head")
    assert "governance_proposals" in inspect(engine).get_table_names()


def test_reporting_restore_migration_is_reversible_and_immutable(tmp_path: Path) -> None:
    database_path = tmp_path / "reporting-restore-migration.db"
    url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert {
        "report_jobs",
        "governance_reports",
        "restore_requests",
        "restore_execution_links",
        "restore_execution_results",
    } <= set(inspector.get_table_names())
    assert "semantic_source_version_id" in {
        column["name"] for column in inspector.get_columns("restore_requests")
    }
    assert {"facts", "facts_hash"} <= {
        column["name"] for column in inspector.get_columns("governance_reports")
    }
    triggers = {
        row[0]
        for row in engine.connect().exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    }
    immutable_tables = {
        "governance_reports",
        "restore_requests",
        "restore_execution_links",
        "restore_execution_results",
    }
    assert {
        f"reject_{table}_{action}"
        for table in immutable_tables
        for action in ("update", "delete")
    } <= triggers
    assert {"reject_report_jobs_fact_update", "reject_report_jobs_delete"} <= triggers

    command.downgrade(config, "0011_plan_explanations")
    assert "restore_requests" not in inspect(engine).get_table_names()

    command.upgrade(config, "head")
    assert "restore_requests" in inspect(engine).get_table_names()

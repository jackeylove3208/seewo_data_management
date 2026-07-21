from io import StringIO
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.models import Base


def test_migration_revision_identifiers_fit_alembic_default_version_column() -> None:
    revision_directory = Path("alembic/versions")
    revision_identifiers = [
        line.split('"')[1]
        for migration in revision_directory.glob("*.py")
        for line in migration.read_text().splitlines()
        if line.startswith("revision: str = ")
    ]

    assert revision_identifiers
    assert all(len(revision) <= 32 for revision in revision_identifiers)


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
        "snapshot_entity_embeddings",
        "difference_items",
        "analysis_results",
        "workflow_stage_runs",
        "governance_proposals",
        "analysis_jobs",
        "analysis_work_items",
        "proposal_batches",
    } <= tables


def test_durable_analysis_work_items_include_created_at(tmp_path: Path) -> None:
    database_path = tmp_path / "durable-analysis-timestamp.db"
    url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")

    columns = {
        column["name"]: column
        for column in inspect(create_engine(url)).get_columns("analysis_work_items")
    }
    assert columns["created_at"]["nullable"] is False


def test_upgrade_reconciles_unversioned_create_all_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "legacy-create-all.db"
    url = f"sqlite:///{database_path}"
    migration_url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    task_id = uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO reconciliation_tasks (
                    id, tenant_id, scope_id, snapshot_mode, entity_types, status,
                    stage, idempotency_key, request_hash, created_at
                ) VALUES (
                    :id, 'school-1', 'all', 'full', '[\"teacher\"]', 'ready',
                    'matching', 'legacy-task', 'legacy-hash', CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": task_id},
        )
        connection.exec_driver_sql("ALTER TABLE analysis_results DROP COLUMN gateway_request_ids")
    assert "alembic_version" not in inspect(engine).get_table_names()

    monkeypatch.setenv("RECONCILIATION_DATABASE_URL", migration_url)
    command.upgrade(Config("alembic.ini"), "head")

    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("analysis_results")}
    assert columns["gateway_request_ids"]["nullable"] is False
    assert columns["gateway_request_ids"]["default"] in {"'[]'", "('[]')"}
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT id FROM reconciliation_tasks WHERE id = :id"),
                {"id": task_id},
            )
            == task_id
        )
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0015_merge_reporting_and_matching_heads"
        )


def test_interrupted_legacy_upgrade_can_be_reapplied(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "interrupted-legacy.db"
    sync_url = f"sqlite:///{database_path}"
    migration_url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_engine(sync_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE analysis_results DROP COLUMN gateway_request_ids")

    monkeypatch.setenv("RECONCILIATION_DATABASE_URL", migration_url)
    command.upgrade(Config("alembic.ini"), "head")
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_workflow_stage_runs_status")
        connection.exec_driver_sql("DROP TRIGGER reject_governance_proposals_delete")
        connection.exec_driver_sql("DROP TRIGGER reject_analysis_results_delete")
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_governance_proposals_delete
            AFTER INSERT ON governance_proposals
            BEGIN
                SELECT 1;
            END
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_analysis_results_delete
            AFTER INSERT ON analysis_results
            BEGIN
                SELECT 1;
            END
            """
        )
        connection.execute(text("UPDATE alembic_version SET version_num = '0005_analysis_results'"))

    command.upgrade(Config("alembic.ini"), "head")

    inspector = inspect(engine)
    assert "workflow_stage_runs" in inspector.get_table_names()
    assert "ix_workflow_stage_runs_status" in {
        index["name"] for index in inspector.get_indexes("workflow_stage_runs")
    }
    assert "gateway_request_ids" in {
        column["name"] for column in inspector.get_columns("analysis_results")
    }
    with engine.connect() as connection:
        triggers = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert {
            "reject_governance_proposals_update",
            "reject_governance_proposals_delete",
        } <= triggers
        delete_trigger_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name = 'reject_governance_proposals_delete'"
            )
        )
        assert "BEFORE DELETE" in delete_trigger_sql
        analysis_delete_trigger_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name = 'reject_analysis_results_delete'"
            )
        )
        assert "BEFORE DELETE" in analysis_delete_trigger_sql
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0015_merge_reporting_and_matching_heads"
        )


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

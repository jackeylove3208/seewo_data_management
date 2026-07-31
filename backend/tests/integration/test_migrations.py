import asyncio
import os
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, delete, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.core.database import Database
from app.models import Base
from app.models.agent_analysis import AgentConnectorCapabilityRecord
from app.models.agent_runtime import AgentRunRecord
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.tasks.deletion_service import TaskDeletionService

MIGRATION_TEST_DATABASE_URL_ENV = "RECONCILIATION_MIGRATION_TEST_DATABASE_URL"
MIGRATION_TEST_DATABASE_NAME = "reconcile_migration_test"


def _migration_test_database_url(value: str) -> URL:
    url = make_url(value)
    if url.drivername != "postgresql+asyncpg":
        raise ValueError("migration test database URL must use PostgreSQL with asyncpg")
    if url.database != MIGRATION_TEST_DATABASE_NAME:
        raise ValueError(
            f"migration test database URL must target {MIGRATION_TEST_DATABASE_NAME!r}"
        )
    return url


async def _recreate_migration_test_database(url: URL) -> None:
    maintenance_url = url.set(
        drivername="postgresql+asyncpg",
        database="postgres",
    )
    engine = create_async_engine(maintenance_url)
    try:
        async with engine.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": MIGRATION_TEST_DATABASE_NAME},
            )
            await connection.execute(
                text(f"DROP DATABASE IF EXISTS {MIGRATION_TEST_DATABASE_NAME}")
            )
            await connection.execute(text(f"CREATE DATABASE {MIGRATION_TEST_DATABASE_NAME}"))
    finally:
        await engine.dispose()


async def _drop_migration_test_database(url: URL) -> None:
    maintenance_url = url.set(
        drivername="postgresql+asyncpg",
        database="postgres",
    )
    engine = create_async_engine(maintenance_url)
    try:
        async with engine.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": MIGRATION_TEST_DATABASE_NAME},
            )
            await connection.execute(
                text(f"DROP DATABASE IF EXISTS {MIGRATION_TEST_DATABASE_NAME}")
            )
    finally:
        await engine.dispose()


async def _migration_test_schema_state(
    url: URL,
) -> tuple[set[str], set[str], set[str], str, int, int, dict[str, int | None]]:
    engine = create_async_engine(url.set(drivername="postgresql+asyncpg"))
    try:
        async with engine.connect() as connection:
            versions = set(
                (await connection.scalars(text("SELECT version_num FROM alembic_version"))).all()
            )
            extensions = set(
                (await connection.scalars(text("SELECT extname FROM pg_extension"))).all()
            )
            tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                ).all()
            )
            agent_analysis_trigger_function = await connection.scalar(
                text(
                    "SELECT pg_get_functiondef("
                    "'reject_agent_analysis_mutation()'::regprocedure)"
                )
            )
            assert agent_analysis_trigger_function is not None
            checkpoint_hash_length = await connection.scalar(
                text(
                    "SELECT character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'agent_checkpoints' "
                    "AND column_name = 'input_hash'"
                )
            )
            assert checkpoint_hash_length is not None
            source_file_storage_name_length = await connection.scalar(
                text(
                    "SELECT character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'source_files' "
                    "AND column_name = 'storage_name'"
                )
            )
            assert source_file_storage_name_length is not None
            mapping_hash_lengths = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name, character_maximum_length "
                            "FROM information_schema.columns "
                            "WHERE table_name = 'agent_database_schema_mappings' "
                            "AND column_name IN ("
                            "'authoritative_schema_fingerprint', "
                            "'target_schema_fingerprint', "
                            "'content_hash'"
                            ")"
                        )
                    )
                ).all()
            )
            return (
                versions,
                extensions,
                tables,
                agent_analysis_trigger_function,
                checkpoint_hash_length,
                source_file_storage_name_length,
                mapping_hash_lengths,
            )
    finally:
        await engine.dispose()


def test_migration_test_database_url_requires_dedicated_postgresql_database() -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        _migration_test_database_url("sqlite:///migration-test.db")

    with pytest.raises(ValueError, match="asyncpg"):
        _migration_test_database_url(
            "postgresql+psycopg://reconcile:reconcile@localhost:5432/reconcile_migration_test"
        )

    with pytest.raises(ValueError, match="reconcile_migration_test"):
        _migration_test_database_url(
            "postgresql+asyncpg://reconcile:reconcile@localhost:5432/reconcile"
        )


def test_clean_postgresql_migration_reaches_head(monkeypatch: pytest.MonkeyPatch) -> None:
    configured_url = os.getenv(MIGRATION_TEST_DATABASE_URL_ENV)
    if configured_url is None:
        pytest.skip(
            f"set {MIGRATION_TEST_DATABASE_URL_ENV} to run the clean PostgreSQL migration test"
        )

    url = _migration_test_database_url(configured_url)
    database_url = url.render_as_string(hide_password=False)
    monkeypatch.setenv("RECONCILIATION_DATABASE_URL", database_url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    expected_heads = set(ScriptDirectory.from_config(config).get_heads())

    asyncio.run(_recreate_migration_test_database(url))
    try:
        command.upgrade(config, "head")
        (
            versions,
            extensions,
            tables,
            trigger_function,
            checkpoint_hash_length,
            source_file_storage_name_length,
            mapping_hash_lengths,
        ) = asyncio.run(
            _migration_test_schema_state(url)
        )
        assert versions == expected_heads
        assert "vector" in extensions
        assert "app.task_deletion" in trigger_function
        assert "TG_OP = 'DELETE'" in trigger_function
        assert checkpoint_hash_length == 71
        assert source_file_storage_name_length == 128
        assert mapping_hash_lengths == {
            "authoritative_schema_fingerprint": 71,
            "target_schema_fingerprint": 71,
            "content_hash": 71,
        }
        assert {
            "agent_graph_runs",
            "agent_graph_candidate_sets",
            "agent_supervisor_decisions",
            "agent_graph_transitions",
            "agent_evidence_manifests",
            "agent_subagent_invocations",
            "agent_tool_calls",
            "agent_human_gates",
            "agent_database_schema_mappings",
            "execution_batches",
            "execution_operations",
            "report_jobs",
            "governance_reports",
            "restore_requests",
            "restore_execution_links",
            "restore_execution_results",
            "remote_sources",
        } <= tables
    finally:
        asyncio.run(_drop_migration_test_database(url))


def test_postgresql_agent_deletion_removes_pre_execution_target_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_url = os.getenv(MIGRATION_TEST_DATABASE_URL_ENV)
    if configured_url is None:
        pytest.skip(
            f"set {MIGRATION_TEST_DATABASE_URL_ENV} to run the PostgreSQL deletion test"
        )

    url = _migration_test_database_url(configured_url)
    database_url = url.render_as_string(hide_password=False)
    monkeypatch.setenv("RECONCILIATION_DATABASE_URL", database_url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    async def exercise() -> None:
        database = Database(database_url)
        try:
            async with database.session_factory() as session:
                task = ReconciliationTask(
                    tenant_id="school-1",
                    scope_id="all",
                    snapshot_mode="full",
                    entity_types=["teacher"],
                    workflow_version="new-agent-v1",
                    status="ready",
                    stage="analysis",
                    idempotency_key=f"postgres-delete-{uuid4()}",
                    request_hash="e" * 64,
                )
                session.add(task)
                await session.flush()
                source_file = SourceFile(
                    task_id=task.id,
                    source_role="target",
                    original_name="target.csv",
                    storage_name="target.csv",
                    storage_path="/tmp/postgres-target.csv",
                    sha256="f" * 64,
                    size_bytes=1,
                )
                session.add(source_file)
                await session.flush()
                snapshot = Snapshot(
                    id=uuid4(),
                    task_id=task.id,
                    source_file_id=source_file.id,
                    source_role="target",
                    schema_version="canonical-v1",
                    mapping_version="target-v1",
                    file_hash="1" * 64,
                    content_hash="2" * 64,
                    summary={},
                )
                run = AgentRunRecord(
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    kind="sync",
                    workflow_version=task.workflow_version,
                    phase="analyze_batches",
                    status="blocked_model_error",
                )
                session.add_all([snapshot, run])
                await session.flush()
                version = TargetVersionRecord(
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    source_snapshot_id=snapshot.id,
                    file_sha256="3" * 64,
                    content_hash="4" * 64,
                    storage_path="/tmp/postgres-target-version.csv",
                )
                session.add(version)
                await session.commit()
                task_id = task.id
                version_id = version.id

            async with database.session_factory() as session:
                await TaskDeletionService(
                    session,
                    Path("storage/uploads/remote"),
                ).delete(task_id, "school-1")

            async with database.session_factory() as session:
                assert await session.get(ReconciliationTask, task_id) is None
                assert await session.get(TargetVersionRecord, version_id) is None
        finally:
            await database.dispose()

    asyncio.run(_recreate_migration_test_database(url))
    try:
        command.upgrade(config, "head")
        asyncio.run(exercise())
    finally:
        asyncio.run(_drop_migration_test_database(url))


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


def test_csv_binding_migration_refuses_a_lossy_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "csv-binding-downgrade.db"
    sync_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(config, "head")
    engine = create_engine(sync_url)
    task_id = uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO reconciliation_tasks (
                    id, tenant_id, scope_id, snapshot_mode, entity_types, status,
                    stage, workflow_version, task_kind, title, agent_intent,
                    idempotency_key, request_hash, created_at
                ) VALUES (
                    :id, 'school-1', 'all', 'full', '[\"student\"]', 'completed',
                    'reporting', 'agent-graph-v1', 'sync', 'CSV 数据库任务',
                    :intent, :key, :request_hash, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": task_id,
                "intent": (
                    '{"source":{"kind":"csv"},'
                    '"target":{"kind":"database","configuration_id":"seewo-data-mysql"}}'
                ),
                "key": f"csv-database-{uuid4()}",
                "request_hash": "e" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO agent_source_bindings (
                    id, tenant_id, task_id, role, connector_kind,
                    configuration_id, snapshot_id, configuration_fingerprint,
                    frozen_public_configuration, credential_reference,
                    mapping_checkpoint_key, normalization_checkpoint_key,
                    created_at
                ) VALUES (
                    :id, 'school-1', :task_id, 'authoritative', 'csv',
                    'upload-1', NULL, :fingerprint, '{}', 'none://csv-authority',
                    'mapping', 'normalization', CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": uuid4().hex,
                "task_id": task_id,
                "fingerprint": "f" * 64,
            },
        )

    with pytest.raises(RuntimeError, match="CSV source bindings"):
        command.downgrade(config, "0041_task_scoped_api_connections")


def test_source_storage_ownership_backfills_local_references_and_guards_downgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "source-storage-ownership.db"
    sync_url = f"sqlite:///{database_path}"
    migration_url = f"sqlite+aiosqlite:///{database_path}"
    monkeypatch.setenv("RECONCILIATION_DATABASE_URL", migration_url)
    config = Config("alembic.ini")
    command.upgrade(config, "0031_agent_reviewable_risk")
    engine = create_engine(sync_url)
    first_task_id = uuid4().hex
    external_path = str(tmp_path / "seewo-original.csv")
    intent = (
        '{"source":{"kind":"csv"},'
        '"target":{"kind":"local","source_ref":"seewo/original.csv"}}'
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO reconciliation_tasks (
                    id, tenant_id, scope_id, snapshot_mode, entity_types, status,
                    stage, workflow_version, task_kind, title, agent_intent,
                    idempotency_key, request_hash, created_at
                ) VALUES (
                    :id, 'school-1', 'all', 'full', '[\"student\"]', 'completed',
                    'reporting', 'agent-graph-v1', 'sync', '旧本地任务', :intent,
                    :key, :request_hash, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": first_task_id,
                "intent": intent,
                "key": f"local-task-{uuid4()}",
                "request_hash": "a" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO source_files (
                    id, task_id, source_role, original_name, storage_name,
                    storage_path, sha256, size_bytes, detected_encoding, created_at
                ) VALUES (
                    :id, :task_id, 'target', 'original.csv', :storage_name,
                    :storage_path, :sha256, 4, 'utf-8', CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": uuid4().hex,
                "task_id": first_task_id,
                "storage_name": f"local-{uuid4().hex}",
                "storage_path": external_path,
                "sha256": "b" * 64,
            },
        )

    command.upgrade(config, "head")

    with engine.begin() as connection:
        assert connection.scalar(
            text(
                "SELECT managed_storage FROM source_files "
                "WHERE task_id = :task_id"
            ),
            {"task_id": first_task_id},
        ) == 0
        second_task_id = uuid4().hex
        connection.execute(
            text(
                """
                INSERT INTO reconciliation_tasks (
                    id, tenant_id, scope_id, snapshot_mode, entity_types, status,
                    stage, workflow_version, task_kind, title, agent_intent,
                    idempotency_key, request_hash, created_at
                ) VALUES (
                    :id, 'school-2', 'all', 'full', '[\"student\"]', 'completed',
                    'reporting', 'agent-graph-v1', 'sync', '第二次本地任务', :intent,
                    :key, :request_hash, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": second_task_id,
                "intent": intent,
                "key": f"local-task-{uuid4()}",
                "request_hash": "c" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO source_files (
                    id, task_id, source_role, original_name, storage_name,
                    storage_path, managed_storage, sha256, size_bytes,
                    detected_encoding, created_at
                ) VALUES (
                    :id, :task_id, 'target', 'original.csv', :storage_name,
                    :storage_path, 0, :sha256, 4, 'utf-8', CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": uuid4().hex,
                "task_id": second_task_id,
                "storage_name": f"local-{uuid4().hex}",
                "storage_path": external_path,
                "sha256": "d" * 64,
            },
        )

    with pytest.raises(
        RuntimeError,
        match="repeated external source references",
    ):
        command.downgrade(config, "0031_agent_reviewable_risk")


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
        "agent_conversations",
        "agent_runs",
        "agent_task_events",
        "agent_checkpoints",
        "agent_failures",
        "school_task_locks",
        "agent_connector_capabilities",
        "agent_input_records",
        "agent_input_marks",
        "agent_identity_postings",
        "agent_identity_evidence",
        "agent_identity_claims",
        "agent_work_items",
        "agent_model_batches",
        "agent_model_batch_items",
        "agent_model_attempts",
        "agent_findings",
        "agent_finding_solutions",
        "agent_finding_dependencies",
        "remote_sources",
    } <= tables

    task_columns = {
        column["name"]: column
        for column in inspect(create_engine(f"sqlite:///{database_path}")).get_columns(
            "reconciliation_tasks"
        )
    }
    assert task_columns["workflow_version"]["nullable"] is False
    run_columns = {
        column["name"]
        for column in inspect(create_engine(f"sqlite:///{database_path}")).get_columns("agent_runs")
    }
    assert "attempt_count" in run_columns
    assert "lease_token" in run_columns
    assert "ingestion_contract_version" in run_columns
    assert "execution_contract_version" in run_columns
    conversation_inspector = inspect(create_engine(f"sqlite:///{database_path}"))
    conversation_columns = {
        column["name"]
        for column in conversation_inspector.get_columns("agent_conversations")
    }
    conversation_schema_names = {
        item["name"]
        for item in [
            *conversation_inspector.get_indexes("agent_conversations"),
            *conversation_inspector.get_unique_constraints("agent_conversations"),
        ]
        if item.get("name")
    }
    assert "reset_idempotency_key" in conversation_columns
    assert "uq_agent_conversation_reset_key" in conversation_schema_names
    assert "uq_agent_conversations_active_operator" in conversation_schema_names
    remote_source_columns = {
        column["name"]
        for column in conversation_inspector.get_columns("remote_sources")
    }
    assert {
        "conversation_id",
        "task_id",
        "source_file_id",
        "original_url",
        "display_origin",
        "state",
        "content_sha256",
        "safe_problem_code",
    } <= remote_source_columns
    checkpoint_columns = {
        column["name"]: column
        for column in inspect(create_engine(f"sqlite:///{database_path}")).get_columns(
            "agent_checkpoints"
        )
    }
    assert checkpoint_columns["input_hash"]["type"].length == 71
    source_file_columns = {
        column["name"]: column
        for column in inspect(create_engine(f"sqlite:///{database_path}")).get_columns(
            "source_files"
        )
    }
    assert source_file_columns["storage_name"]["type"].length == 128


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


def test_agent_csv_migration_preserves_seeded_legacy_task_and_guards_new_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "agent-csv-history.db"
    url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0019_agent_lease_fencing")
    engine = create_engine(url)
    task_id = uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO reconciliation_tasks (
                    id, tenant_id, scope_id, snapshot_mode, entity_types, status,
                    stage, idempotency_key, request_hash, workflow_version, created_at
                ) VALUES (
                    :id, 'school-1', 'all', 'full', '["student"]', 'ready',
                    'matching', 'seeded-legacy-task', 'legacy-hash', 'legacy-v1',
                    CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": task_id},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT id, workflow_version FROM reconciliation_tasks WHERE id = :id"),
            {"id": task_id},
        ).one()
        assert row == (task_id, "legacy-v1")
        triggers = {
            value
            for value in connection.scalars(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' AND name LIKE 'reject_agent_%'"
                )
            )
        }
        assert "reject_agent_input_records_update" in triggers
        assert "reject_agent_model_attempts_update" in triggers
        assert "reject_agent_model_attempts_delete" in triggers
        assert "agent_task_deletion_guard" in inspect(engine).get_table_names()
        delete_trigger_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name = 'reject_agent_model_attempts_delete'"
            )
        )
        assert "agent_task_deletion_guard" in delete_trigger_sql
        run_id = uuid4().hex
        capability_id = uuid4().hex
        connection.execute(
            text(
                """
                INSERT INTO agent_runs (
                    id, task_id, tenant_id, kind, workflow_version, phase, status,
                    version, attempt_count, progress_completed, progress_total,
                    created_at, updated_at
                ) VALUES (
                    :id, :task_id, 'school-1', 'sync', 'new-agent-v1',
                    'analyze_batches', 'blocked_model_error', 1, 0, 0, 0,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": run_id, "task_id": task_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO agent_connector_capabilities (
                    id, run_id, task_id, tenant_id, source_role, connector_kind,
                    capability_hash, capabilities, created_at
                ) VALUES (
                    :id, :run_id, :task_id, 'school-1', 'authoritative', 'csv',
                    :capability_hash, '{"read": true}', CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": capability_id,
                "run_id": run_id,
                "task_id": task_id,
                "capability_hash": "a" * 64,
            },
        )
        connection.commit()
        with pytest.raises(IntegrityError, match="append-only"):
            connection.execute(
                text(
                    "DELETE FROM agent_connector_capabilities "
                    "WHERE id = :capability_id"
                ),
                {"capability_id": capability_id},
            )
        connection.rollback()
        connection.execute(
            text(
                "UPDATE agent_task_deletion_guard "
                "SET task_id = :task_id WHERE id = 1"
            ),
            {"task_id": uuid4().hex},
        )
        connection.commit()
        with pytest.raises(IntegrityError, match="append-only"):
            connection.execute(
                text(
                    "DELETE FROM agent_connector_capabilities "
                    "WHERE id = :capability_id"
                ),
                {"capability_id": capability_id},
            )
        connection.rollback()
        connection.execute(
            text(
                "UPDATE agent_task_deletion_guard "
                "SET task_id = :task_id WHERE id = 1"
            ),
            {"task_id": task_id},
        )
        connection.execute(
            text(
                "DELETE FROM agent_connector_capabilities "
                "WHERE id = :capability_id"
            ),
            {"capability_id": capability_id},
        )
        connection.execute(
            text("UPDATE agent_task_deletion_guard SET task_id = NULL WHERE id = 1")
        )
        connection.commit()


def test_task_deletion_service_uses_scoped_guard_on_migrated_sqlite(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "scoped-agent-deletion.db"
    sync_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(config, "head")

    async def exercise() -> None:
        database = Database(f"sqlite+aiosqlite:///{database_path}")
        try:
            async with database.session_factory() as session:
                removable = ReconciliationTask(
                    tenant_id="school-1",
                    scope_id="all",
                    snapshot_mode="full",
                    entity_types=["teacher"],
                    workflow_version="new-agent-v1",
                    status="ready",
                    stage="analysis",
                    idempotency_key=f"removable-{uuid4()}",
                    request_hash="a" * 64,
                )
                survivor = ReconciliationTask(
                    tenant_id="school-1",
                    scope_id="all",
                    snapshot_mode="full",
                    entity_types=["teacher"],
                    workflow_version="new-agent-v1",
                    status="ready",
                    stage="analysis",
                    idempotency_key=f"survivor-{uuid4()}",
                    request_hash="b" * 64,
                )
                session.add_all([removable, survivor])
                await session.flush()
                removable_run = AgentRunRecord(
                    task_id=removable.id,
                    tenant_id=removable.tenant_id,
                    kind="sync",
                    workflow_version=removable.workflow_version,
                    phase="analyze_batches",
                    status="blocked_model_error",
                )
                survivor_run = AgentRunRecord(
                    task_id=survivor.id,
                    tenant_id=survivor.tenant_id,
                    kind="sync",
                    workflow_version=survivor.workflow_version,
                    phase="analyze_batches",
                    status="blocked_model_error",
                )
                session.add_all([removable_run, survivor_run])
                await session.flush()
                removable_capability = AgentConnectorCapabilityRecord(
                    run_id=removable_run.id,
                    task_id=removable.id,
                    tenant_id=removable.tenant_id,
                    source_role="authoritative",
                    connector_kind="csv",
                    capability_hash="c" * 64,
                    capabilities={"read": True},
                )
                survivor_capability = AgentConnectorCapabilityRecord(
                    run_id=survivor_run.id,
                    task_id=survivor.id,
                    tenant_id=survivor.tenant_id,
                    source_role="authoritative",
                    connector_kind="csv",
                    capability_hash="d" * 64,
                    capabilities={"read": True},
                )
                session.add_all([removable_capability, survivor_capability])
                await session.commit()
                removable_id = removable.id
                survivor_id = survivor.id
                survivor_capability_id = survivor_capability.id

            async with database.session_factory() as session:
                await TaskDeletionService(
                    session,
                    Path("storage/uploads/remote"),
                ).delete(removable_id, "school-1")

            async with database.session_factory() as session:
                assert await session.get(ReconciliationTask, removable_id) is None
                assert await session.get(ReconciliationTask, survivor_id) is not None
                assert (
                    await session.get(
                        AgentConnectorCapabilityRecord,
                        survivor_capability_id,
                    )
                    is not None
                )
                with pytest.raises(IntegrityError, match="append-only"):
                    await session.execute(
                        delete(AgentConnectorCapabilityRecord).where(
                            AgentConnectorCapabilityRecord.id
                            == survivor_capability_id
                        )
                    )
        finally:
            await database.dispose()

    asyncio.run(exercise())


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
            connection.scalar(
                text("SELECT workflow_version FROM reconciliation_tasks WHERE id = :id"),
                {"id": task_id},
            )
            == "legacy-v1"
        )
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) in set(
            ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
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
        connection.exec_driver_sql("DROP TRIGGER reject_analysis_results_delete")
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
        assert "reject_governance_proposals_update" in triggers
        assert "reject_governance_proposals_delete" not in triggers
        analysis_delete_trigger_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name = 'reject_analysis_results_delete'"
            )
        )
        assert "BEFORE DELETE" in analysis_delete_trigger_sql
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) in set(
            ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
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


def test_governance_proposal_migration_is_reversible_and_update_immutable(tmp_path: Path) -> None:
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
    assert "reject_governance_proposals_update" in triggers
    assert "reject_governance_proposals_delete" not in triggers

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
        f"reject_{table}_{action}" for table in immutable_tables for action in ("update", "delete")
    } <= triggers
    assert {"reject_report_jobs_fact_update", "reject_report_jobs_delete"} <= triggers

    command.downgrade(config, "0011_plan_explanations")
    assert "restore_requests" not in inspect(engine).get_table_names()

    command.upgrade(config, "head")
    assert "restore_requests" in inspect(engine).get_table_names()

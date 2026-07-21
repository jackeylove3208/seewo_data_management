import asyncio
import os
from logging.config import fileConfig

from alembic.script import ScriptDirectory
from sqlalchemy import engine_from_config, inspect, pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.models import Base

config = context.config
configured_database_url = os.getenv("RECONCILIATION_DATABASE_URL")
if configured_database_url:
    config.set_main_option("sqlalchemy.url", configured_database_url.replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_LEGACY_BASELINES = (
    ("analysis_results", "0005_analysis_results"),
    ("difference_items", "0004_differences"),
    ("target_entity_embeddings", "0003_target_embeddings"),
    ("entity_mappings", "0002_entity_resolution"),
    ("reconciliation_tasks", "0001_ingestion"),
)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _stamp_legacy_create_all_schema(connection: Connection) -> None:
    tables = set(inspect(connection).get_table_names())
    if "alembic_version" in tables:
        return
    baseline = next(
        (revision for table, revision in _LEGACY_BASELINES if table in tables),
        None,
    )
    if baseline is None:
        return
    context.get_context().stamp(ScriptDirectory.from_config(config), baseline)


def configure_and_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        _stamp_legacy_create_all_schema(connection)
        context.run_migrations()


def run_sync_migrations() -> None:
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        configure_and_run(connection)


async def run_async_migrations() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(configure_and_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
elif make_url(config.get_main_option("sqlalchemy.url")).get_dialect().is_async:
    asyncio.run(run_async_migrations())
else:
    run_sync_migrations()

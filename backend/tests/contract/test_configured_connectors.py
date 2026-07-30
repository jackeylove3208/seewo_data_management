import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, insert
from sqlalchemy.ext.asyncio import create_async_engine

from app.connectors.configured import (
    ApiConnectorConfiguration,
    ConfiguredApiConnector,
    ConnectorCapabilities,
    ConnectorCapabilityError,
    ConnectorConflictError,
    DatabaseConnectorConfiguration,
    SqlAlchemyConnectorStore,
)
from tests.fixtures.connector_store import InMemoryConnectorStore


def test_database_configuration_accepts_only_server_owned_identifiers() -> None:
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-db",
        table_name="seewo_people",
        primary_key="person_id",
        version_column="row_version",
        field_columns={"category": "category", "name": "name", "number": "number"},
    )

    assert configuration.credential_reference == "secret://connectors/seewo-db"
    with pytest.raises(ValueError, match="identifier"):
        DatabaseConnectorConfiguration(
            credential_reference="secret://connectors/seewo-db",
            table_name="people; DROP TABLE people",
            primary_key="id",
            version_column="version",
            field_columns={"name": "name"},
        )


@pytest.mark.asyncio
async def test_api_connector_reads_all_stable_pages_without_exposing_credentials() -> None:
    configuration = ApiConnectorConfiguration(
        credential_reference="secret://connectors/third-party-api",
        endpoint="https://connector.example.test/v1/people",
        record_id_field="id",
        version_field="etag",
        capabilities=ConnectorCapabilities(read=True, paginated=True),
    )
    store = InMemoryConnectorStore(
        records=[
            {"id": "2", "etag": "v1", "name": "李四"},
            {"id": "1", "etag": "v3", "name": "张三"},
        ],
    )
    connector = ConfiguredApiConnector(configuration=configuration, store=store)

    rows = [row async for page in connector.read_pages(page_size=1) for row in page.records]

    assert [row["id"] for row in rows] == ["1", "2"]
    assert (await connector.version()).value == "v3"
    assert "do-not-log-this" not in str(await connector.health())


@pytest.mark.asyncio
async def test_target_write_requires_declared_capability_idempotency_and_current_version() -> None:
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-db",
        table_name="seewo_people",
        primary_key="id",
        version_column="version",
        field_columns={"name": "name"},
        capabilities=ConnectorCapabilities(read=True, update=True, optimistic_version=True),
    )
    store = InMemoryConnectorStore(records=[{"id": "student-1", "version": "1", "name": "旧名"}])
    connector = ConfiguredApiConnector(configuration=configuration, store=store)

    version = await connector.version()
    output = await connector.apply(
        [
            {
                "operation": "update",
                "id": "student-1",
                "before": {"name": "旧名"},
                "after": {"name": "新名"},
            }
        ],
        idempotency_key="plan-1:operation-1",
        expected_version=version.value,
    )

    assert output.value != version.value
    assert await connector.verify([{"id": "student-1", "after": {"name": "新名"}}]) == [True]
    with pytest.raises(ConnectorConflictError):
        await connector.apply(
            [
                {
                    "operation": "update",
                    "id": "student-1",
                    "before": {"name": "新名"},
                    "after": {"name": "再次修改"},
                }
            ],
            idempotency_key="plan-2:operation-1",
            expected_version=version.value,
        )


@pytest.mark.asyncio
async def test_authoritative_connector_rejects_writes_even_when_store_can_mutate() -> None:
    configuration = ApiConnectorConfiguration(
        credential_reference="secret://connectors/authority-api",
        endpoint="https://connector.example.test/v1/people",
        record_id_field="id",
        version_field="version",
        source_role="authoritative",
        capabilities=ConnectorCapabilities(read=True, update=True, optimistic_version=True),
    )
    connector = ConfiguredApiConnector(
        configuration=configuration,
        store=InMemoryConnectorStore(records=[{"id": "teacher-1", "version": "1"}]),
    )

    with pytest.raises(ConnectorCapabilityError, match="authoritative"):
        await connector.apply(
            [{"operation": "update", "id": "teacher-1", "after": {"name": "不允许"}}],
            idempotency_key="forbidden",
            expected_version=(await connector.version()).value,
        )


@pytest.mark.asyncio
async def test_database_store_reads_only_configured_table_and_stable_primary_key() -> None:
    metadata = MetaData()
    people = Table(
        "seewo_people",
        metadata,
        Column("id", String, primary_key=True),
        Column("version", String, nullable=False),
        Column("name", String, nullable=False),
        Column("home_address", String, nullable=True),
    )
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(people),
            [
                {
                    "id": "2",
                    "version": "v1",
                    "name": "李四",
                    "home_address": "不应离开连接器",
                },
                {
                    "id": "1",
                    "version": "v3",
                    "name": "张三",
                    "home_address": "不应离开连接器",
                },
            ],
        )
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-db",
        table_name="seewo_people",
        primary_key="id",
        version_column="version",
        field_columns={"name": "name"},
        allowed_columns=("id", "version", "name"),
        capabilities=ConnectorCapabilities(read=True, paginated=True),
    )
    store = SqlAlchemyConnectorStore(engine=engine, table=people, configuration=configuration)
    connector = ConfiguredApiConnector(configuration=configuration, store=store)

    rows = [row["id"] async for page in connector.read_pages(page_size=1) for row in page.records]
    assert rows == ["1", "2"]
    pages = [
        row
        async for page in connector.read_pages(
            page_size=2,
            fields=("name",),
        )
        for row in page.records
    ]
    assert [set(row) for row in pages] == [
        {"id", "version", "name"},
        {"id", "version", "name"},
    ]
    assert (await connector.version()).value == "v3"
    schema = await connector.discover_schema()
    assert schema.fields == (
        "home_address",
        "id",
        "name",
        "version",
    )
    assert schema.field_types["id"].startswith("VARCHAR")
    assert schema.nullable_fields == ("home_address",)
    await engine.dispose()


@pytest.mark.asyncio
async def test_llm_database_connector_exposes_full_schema_before_freezing_row_access() -> None:
    metadata = MetaData()
    people = Table(
        "seewo_people",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("version", String, nullable=False),
        Column("full_name", String, nullable=False),
        Column("home_address", String, nullable=True),
    )
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(people),
            {
                "version": "v1",
                "full_name": "张三",
                "home_address": "不得暴露",
            },
        )
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-db",
        table_name="seewo_people",
        primary_key="id",
        version_column="version",
        mapping={"mode": "llm"},
        capabilities=ConnectorCapabilities(
            read=True,
            paginated=True,
            update=True,
            optimistic_version=True,
        ),
    )
    connector = ConfiguredApiConnector(
        configuration=configuration,
        store=SqlAlchemyConnectorStore(engine=engine, table=people, configuration=configuration),
    )

    schema = await connector.discover_schema()

    assert schema.fields == ("full_name", "home_address", "id", "version")
    assert {
        column.name: (
            column.sql_type,
            column.nullable,
            column.primary_key,
            column.generated,
            column.autoincrement,
        )
        for column in schema.columns
    } == {
        "full_name": ("VARCHAR", False, False, False, False),
        "home_address": ("VARCHAR", True, False, False, False),
        "id": ("INTEGER", False, True, True, True),
        "version": ("VARCHAR", False, False, False, False),
    }
    with pytest.raises(ConnectorCapabilityError, match="frozen mapping"):
        _ = [row async for page in connector.read_pages() for row in page.records]
    with pytest.raises(ConnectorCapabilityError, match="frozen mapping"):
        await connector.read_record("1")
    with pytest.raises(ConnectorCapabilityError, match="frozen mapping"):
        await connector.apply(
            [{"operation": "update", "id": "1", "after": {"name": "李四"}}],
            idempotency_key="unfrozen-update",
            expected_version="v1",
        )
    with pytest.raises(ConnectorCapabilityError, match="frozen mapping"):
        await connector.verify([{"id": "1", "after": {"name": "张三"}}])
    await engine.dispose()


@pytest.mark.asyncio
async def test_llm_database_connector_limits_rows_to_frozen_mapping() -> None:
    metadata = MetaData()
    people = Table(
        "seewo_people",
        metadata,
        Column("id", String, primary_key=True),
        Column("version", String, nullable=False),
        Column("full_name", String, nullable=False),
        Column("home_address", String, nullable=True),
    )
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(people),
            {
                "id": "student-1",
                "version": "v1",
                "full_name": "张三",
                "home_address": "不得暴露",
            },
        )
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-db",
        table_name="seewo_people",
        primary_key="id",
        version_column="version",
        mapping={"mode": "llm"},
        capabilities=ConnectorCapabilities(read=True, paginated=True),
    )
    connector = ConfiguredApiConnector(
        configuration=configuration,
        store=SqlAlchemyConnectorStore(engine=engine, table=people, configuration=configuration),
    ).with_frozen_mapping({"name": "full_name"})

    rows = [row async for page in connector.read_pages() for row in page.records]

    assert rows == [{"id": "student-1", "version": "v1", "full_name": "张三"}]
    assert await connector.read_record("student-1") == rows[0]
    await engine.dispose()


@pytest.mark.asyncio
async def test_llm_database_connector_rejects_unknown_or_duplicate_frozen_columns() -> None:
    metadata = MetaData()
    people = Table(
        "seewo_people",
        metadata,
        Column("id", String, primary_key=True),
        Column("version", String, nullable=False),
        Column("full_name", String, nullable=False),
    )
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-db",
        table_name="seewo_people",
        primary_key="id",
        version_column="version",
        mapping={"mode": "llm"},
    )
    connector = ConfiguredApiConnector(
        configuration=configuration,
        store=SqlAlchemyConnectorStore(engine=engine, table=people, configuration=configuration),
    )

    with pytest.raises(ConnectorCapabilityError, match="unavailable"):
        connector.with_frozen_mapping({"name": "invented"})
    with pytest.raises(ConnectorCapabilityError, match="duplicate"):
        connector.with_frozen_mapping({"name": "full_name", "number": "full_name"})
    await engine.dispose()


@pytest.mark.asyncio
async def test_database_store_applies_allow_listed_update_with_version_precondition() -> None:
    metadata = MetaData()
    people = Table(
        "seewo_people",
        metadata,
        Column("id", String, primary_key=True),
        Column("version", String, nullable=False),
        Column("name", String, nullable=False),
    )
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(people), {"id": "student-1", "version": "v1", "name": "旧名"}
        )
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-db",
        table_name="seewo_people",
        primary_key="id",
        version_column="version",
        field_columns={"name": "name"},
        capabilities=ConnectorCapabilities(read=True, update=True, optimistic_version=True),
    )
    connector = ConfiguredApiConnector(
        configuration=configuration,
        store=SqlAlchemyConnectorStore(engine=engine, table=people, configuration=configuration),
    )

    output = await connector.apply(
        [
            {
                "operation": "update",
                "id": "student-1",
                "before": {"name": "旧名"},
                "after": {"name": "新名"},
            }
        ],
        idempotency_key="agent-plan-1-operation-1",
        expected_version="v1",
    )

    assert output.value != "v1"
    assert await connector.verify([{"id": "student-1", "after": {"name": "新名"}}]) == [True]
    assert (
        await connector.apply(
            [
                {
                    "operation": "update",
                    "id": "student-1",
                    "before": {"name": "旧名"},
                    "after": {"name": "新名"},
                }
            ],
            idempotency_key="agent-plan-1-operation-1",
            expected_version="v1",
        )
    ).value == output.value
    await engine.dispose()


@pytest.mark.asyncio
async def test_database_store_keeps_version_token_stable_across_sequential_updates() -> None:
    metadata = MetaData()
    people = Table(
        "seewo_people",
        metadata,
        Column("id", String, primary_key=True),
        Column("version", String, nullable=False),
        Column("name", String, nullable=False),
    )
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(people),
            [
                {"id": "student-1", "version": "v1", "name": "旧名一"},
                {"id": "student-2", "version": "v1", "name": "旧名二"},
            ],
        )
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-db",
        table_name="seewo_people",
        primary_key="id",
        version_column="version",
        field_columns={"name": "name"},
        capabilities=ConnectorCapabilities(
            read=True,
            update=True,
            delete=True,
            optimistic_version=True,
        ),
    )
    connector = ConfiguredApiConnector(
        configuration=configuration,
        store=SqlAlchemyConnectorStore(engine=engine, table=people, configuration=configuration),
    )

    first = await connector.apply(
        [
            {
                "operation": "update",
                "id": "student-1",
                "before": {"name": "旧名一"},
                "after": {"name": "新名一"},
            }
        ],
        idempotency_key="one-0",
        expected_version="v1",
    )
    second = await connector.apply(
        [
            {
                "operation": "update",
                "id": "student-2",
                "before": {"name": "旧名二"},
                "after": {"name": "新名二"},
            }
        ],
        idempotency_key="two-1",
        expected_version=first.value,
    )

    assert (await connector.version()).value == second.value
    deleted = await connector.apply(
        [
            {
                "operation": "delete",
                "id": "student-2",
                "before": {"name": "新名二"},
            }
        ],
        idempotency_key="delete-current-version",
        expected_version=second.value,
    )
    assert (await connector.version()).value == deleted.value
    await engine.dispose()


@pytest.mark.asyncio
async def test_database_store_translates_fixed_contract_fields_to_physical_columns() -> None:
    metadata = MetaData()
    people = Table(
        "seewo_people",
        metadata,
        Column("id", String, primary_key=True),
        Column("version", String, nullable=False),
        Column("full_name", String, nullable=False),
    )
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(people),
            {"id": "student-1", "version": "v1", "full_name": "旧名"},
        )
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-db",
        table_name="seewo_people",
        primary_key="id",
        version_column="version",
        field_columns={"name": "full_name"},
        capabilities=ConnectorCapabilities(
            read=True,
            update=True,
            optimistic_version=True,
        ),
    )
    connector = ConfiguredApiConnector(
        configuration=configuration,
        store=SqlAlchemyConnectorStore(
            engine=engine,
            table=people,
            configuration=configuration,
        ),
    )

    await connector.apply(
        [
            {
                "operation": "update",
                "id": "student-1",
                "before": {"name": "旧名"},
                "after": {"name": "新名"},
            }
        ],
        idempotency_key="semantic-field-update",
        expected_version="v1",
    )

    assert await connector.verify([{"id": "student-1", "after": {"name": "新名"}}]) == [True]
    assert (await connector.read_record("student-1"))["full_name"] == "新名"
    await engine.dispose()

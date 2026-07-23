import pytest
from httpx import AsyncClient, MockTransport, Request, Response
from sqlalchemy import Column, MetaData, String, Table, insert
from sqlalchemy.ext.asyncio import create_async_engine

from app.connectors.configured import (
    ApiConnectorConfiguration,
    ConfiguredApiConnector,
    ConnectorCapabilities,
    ConnectorCapabilityError,
    ConnectorConflictError,
    DatabaseConnectorConfiguration,
    HttpJsonConnectorStore,
    InMemoryConnectorStore,
    SqlAlchemyConnectorStore,
    StaticCredentialResolver,
)


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
        credential="do-not-log-this",
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
async def test_http_store_uses_server_side_credential_reference_and_cursor_protocol() -> None:
    seen_headers: list[str | None] = []

    def handler(request: Request) -> Response:
        seen_headers.append(request.headers.get("authorization"))
        if request.method == "HEAD":
            return Response(200, headers={"etag": "v7"})
        if request.url.params.get("cursor") is None:
            return Response(
                200,
                json={"cursor": None, "items": [{"id": "1"}], "next_cursor": "next"},
            )
        return Response(200, json={"cursor": "next", "items": [{"id": "2"}], "next_cursor": None})

    client = AsyncClient(transport=MockTransport(handler))
    store = HttpJsonConnectorStore(
        client=client,
        configuration=ApiConnectorConfiguration(
            credential_reference="secret://connectors/third-party-api",
            endpoint="https://connector.example.test/v1/people",
            record_id_field="id",
            version_field="etag",
            capabilities=ConnectorCapabilities(read=True, paginated=True),
        ),
        credentials=StaticCredentialResolver(
            {"secret://connectors/third-party-api": "Bearer test-token"}
        ),
    )
    connector = ConfiguredApiConnector(configuration=store.configuration, store=store)

    assert (await connector.version()).value == "v7"
    rows = [row["id"] async for page in connector.read_pages(page_size=1) for row in page.records]
    assert rows == ["1", "2"]
    assert seen_headers == ["Bearer test-token", "Bearer test-token", "Bearer test-token"]
    await client.aclose()


@pytest.mark.asyncio
async def test_http_page_failure_is_actionable_and_does_not_expose_credential() -> None:
    def handler(request: Request) -> Response:
        if request.url.params.get("cursor") is None:
            return Response(
                200,
                json={"cursor": None, "items": [{"id": "1"}], "next_cursor": "next"},
            )
        return Response(503, json={"detail": "upstream failed"})

    client = AsyncClient(transport=MockTransport(handler))
    store = HttpJsonConnectorStore(
        client=client,
        configuration=ApiConnectorConfiguration(
            credential_reference="secret://connectors/third-party-api",
            endpoint="https://connector.example.test/v1/people",
            record_id_field="id",
            version_field="etag",
            capabilities=ConnectorCapabilities(read=True, paginated=True),
        ),
        credentials=StaticCredentialResolver(
            {"secret://connectors/third-party-api": "Bearer never-expose-me"}
        ),
    )
    connector = ConfiguredApiConnector(configuration=store.configuration, store=store)

    with pytest.raises(ConnectorCapabilityError) as error:
        _ = [row async for page in connector.read_pages(page_size=1) for row in page.records]

    assert "never-expose-me" not in str(error.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_http_malformed_page_is_rejected_instead_of_being_treated_as_empty() -> None:
    client = AsyncClient(
        transport=MockTransport(lambda request: Response(200, json={"items": "not-a-list"}))
    )
    store = HttpJsonConnectorStore(
        client=client,
        configuration=ApiConnectorConfiguration(
            credential_reference="secret://connectors/third-party-api",
            endpoint="https://connector.example.test/v1/people",
            record_id_field="id",
            version_field="etag",
        ),
        credentials=StaticCredentialResolver({"secret://connectors/third-party-api": "secret"}),
    )

    with pytest.raises(ConnectorCapabilityError, match="invalid page"):
        await store.page(cursor=None, page_size=10, record_id_field="id")
    await client.aclose()


@pytest.mark.asyncio
async def test_database_store_reads_only_configured_table_and_stable_primary_key() -> None:
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
                {"id": "2", "version": "v1", "name": "李四"},
                {"id": "1", "version": "v3", "name": "张三"},
            ],
        )
    configuration = DatabaseConnectorConfiguration(
        credential_reference="secret://connectors/seewo-db",
        table_name="seewo_people",
        primary_key="id",
        version_column="version",
        field_columns={"name": "name"},
        capabilities=ConnectorCapabilities(read=True, paginated=True),
    )
    store = SqlAlchemyConnectorStore(engine=engine, table=people, configuration=configuration)
    connector = ConfiguredApiConnector(configuration=configuration, store=store)

    rows = [row["id"] async for page in connector.read_pages(page_size=1) for row in page.records]
    assert rows == ["1", "2"]
    assert (await connector.version()).value == "v3"
    assert (await connector.discover_schema()).fields == ("id", "name", "version")
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

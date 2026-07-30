"""Server-owned SQL connector construction for durable Agent workers."""

from typing import Protocol

from sqlalchemy import MetaData, Table
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.connectors.configured import (
    ConfiguredApiConnector,
    ConnectorCapabilityError,
    DatabaseConnectorConfiguration,
    SqlAlchemyConnectorStore,
)
from app.core.config import Settings


class DatabaseConnectorResolver(Protocol):
    async def connector(self, connector_id: str) -> ConfiguredApiConnector: ...


class ConfiguredDatabaseConnectorRuntime:
    """Resolve opaque connector IDs to bounded SQLAlchemy connector façades."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engines: dict[str, AsyncEngine] = {}
        self._connectors: dict[str, ConfiguredApiConnector] = {}

    async def connector(self, connector_id: str) -> ConfiguredApiConnector:
        configuration = self._settings.database_connector_configurations.get(connector_id)
        if configuration is None:
            raise ConnectorCapabilityError("database connector is not configured by the server")
        return await self.connector_for_configuration(connector_id, configuration)

    async def connector_for_configuration(
        self,
        connector_id: str,
        configuration: DatabaseConnectorConfiguration,
    ) -> ConfiguredApiConnector:
        cache_key = f"{connector_id}:{configuration.model_dump_json()}"
        existing = self._connectors.get(cache_key)
        if existing is not None:
            return existing
        credential = self._settings.database_connector_credentials.get(
            configuration.credential_reference
        )
        if credential is None:
            raise ConnectorCapabilityError("database connector credential reference is unavailable")
        dsn = credential.get_secret_value()
        self._validate_dsn(configuration, dsn)
        engine = create_async_engine(dsn, pool_pre_ping=True)
        try:
            metadata = MetaData()

            def reflect_table(sync_connection: Connection) -> Table:
                return Table(
                    configuration.table_name,
                    metadata,
                    schema=configuration.schema_name,
                    autoload_with=sync_connection,
                )

            async with engine.connect() as connection:
                table = await connection.run_sync(reflect_table)
        except Exception as error:
            await engine.dispose()
            raise ConnectorCapabilityError("database connector schema discovery failed") from error
        connector = ConfiguredApiConnector(
            configuration=configuration,
            store=SqlAlchemyConnectorStore(
                engine=engine,
                table=table,
                configuration=configuration,
            ),
        )
        self._engines[cache_key] = engine
        self._connectors[cache_key] = connector
        return connector

    async def close(self) -> None:
        engines = tuple(self._engines.values())
        self._engines.clear()
        self._connectors.clear()
        for engine in engines:
            await engine.dispose()

    @staticmethod
    def _validate_dsn(
        configuration: DatabaseConnectorConfiguration,
        dsn: str,
    ) -> None:
        try:
            url = make_url(dsn)
        except Exception as error:
            raise ConnectorCapabilityError(
                "database connector credential is not a valid SQLAlchemy URL"
            ) from error
        expected_driver = {
            "mysql": "mysql+asyncmy",
            "postgresql": "postgresql+asyncpg",
        }[configuration.dialect]
        if url.drivername != expected_driver:
            raise ConnectorCapabilityError(
                "database connector driver does not match its configured dialect"
            )
        if configuration.database_name is not None and url.database != configuration.database_name:
            raise ConnectorCapabilityError(
                "database connector URL targets a non-allow-listed database"
            )

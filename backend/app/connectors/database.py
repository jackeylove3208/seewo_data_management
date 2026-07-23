from collections.abc import AsyncIterator

from app.connectors.base import (
    ConnectorNotConfigured,
    ConnectorReadRequest,
    ConnectorVersion,
)
from app.connectors.configured import (
    ConfiguredApiConnector,
    ConnectorHealth,
    ConnectorPage,
    ConnectorSchema,
)
from app.schemas.ingestion import ConnectorReadResult


class DatabaseSourceConnector:
    """Server-configured database source for new Agent connector workflows."""

    def __init__(self, *, configured: ConfiguredApiConnector | None = None) -> None:
        self._configured = configured

    def _connector(self) -> ConfiguredApiConnector:
        if self._configured is None:
            raise ConnectorNotConfigured(
                "database source requires a server-side configuration and credential reference"
            )
        return self._configured

    async def version(self) -> ConnectorVersion:
        return await self._connector().version()

    async def health(self) -> ConnectorHealth:
        return await self._connector().health()

    async def schema(self) -> ConnectorSchema:
        return await self._connector().discover_schema()

    async def read_pages(self, *, page_size: int = 100) -> AsyncIterator[ConnectorPage]:
        async for page in self._connector().read_pages(page_size=page_size):
            yield page

    async def read(self, request: ConnectorReadRequest) -> ConnectorReadResult:
        raise ConnectorNotConfigured(
            "database source records must be projected through the Agent ingestion contract"
        )

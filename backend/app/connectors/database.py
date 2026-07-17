from app.connectors.base import (
    ConnectorNotConfigured,
    ConnectorReadRequest,
    ConnectorVersion,
)
from app.schemas.ingestion import ConnectorReadResult


class DatabaseSourceConnector:
    """Explicit extension point for a future database-backed source."""

    async def version(self) -> ConnectorVersion:
        raise ConnectorNotConfigured(
            "database source DSN, query, pagination, and version strategy are not configured"
        )

    async def read(self, request: ConnectorReadRequest) -> ConnectorReadResult:
        raise ConnectorNotConfigured(
            "database source DSN, query, pagination, and version strategy are not configured"
        )

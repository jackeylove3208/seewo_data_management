from app.connectors.base import (
    ConnectorNotConfigured,
    ConnectorReadRequest,
    ConnectorVersion,
)
from app.schemas.ingestion import ConnectorReadResult


class ThirdPartyApiConnector:
    async def version(self) -> ConnectorVersion:
        raise ConnectorNotConfigured(
            "third-party API authentication and endpoint contract are not configured"
        )

    async def read(self, request: ConnectorReadRequest) -> ConnectorReadResult:
        raise ConnectorNotConfigured(
            "third-party API authentication and endpoint contract are not configured"
        )

from typing import Any

from app.connectors.base import (
    ConnectorNotConfigured,
    ConnectorReadRequest,
    ConnectorVersion,
)
from app.schemas.ingestion import ConnectorReadResult


class SeewoApiConnector:
    async def version(self) -> ConnectorVersion:
        raise ConnectorNotConfigured(
            "Seewo API authentication and endpoint contract are not configured"
        )

    async def read(self, request: ConnectorReadRequest) -> ConnectorReadResult:
        raise ConnectorNotConfigured(
            "Seewo API authentication and endpoint contract are not configured"
        )

    async def apply(
        self,
        operations: list[dict[str, Any]],
        idempotency_key: str,
    ) -> ConnectorVersion:
        raise ConnectorNotConfigured(
            "Seewo API authentication and endpoint contract are not configured"
        )

    async def verify(self, expected: list[dict[str, Any]]) -> list[bool]:
        raise ConnectorNotConfigured(
            "Seewo API authentication and endpoint contract are not configured"
        )

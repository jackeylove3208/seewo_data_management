from app.connectors.base import SourceConnector


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, SourceConnector] = {}

    def register(self, name: str, connector: SourceConnector) -> None:
        if name in self._connectors:
            raise ValueError(f"connector already registered: {name}")
        self._connectors[name] = connector

    def get(self, name: str) -> SourceConnector:
        try:
            return self._connectors[name]
        except KeyError as error:
            raise LookupError(f"unknown connector: {name}") from error

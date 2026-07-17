from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from app.schemas.canonical_entities import EntityType
from app.schemas.ingestion import ConnectorReadResult


class ConnectorReadRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_types: frozenset[EntityType] | None = None


class ConnectorVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str


@runtime_checkable
class SourceConnector(Protocol):
    async def version(self) -> ConnectorVersion: ...

    async def read(self, request: ConnectorReadRequest) -> ConnectorReadResult: ...


@runtime_checkable
class TargetConnector(SourceConnector, Protocol):
    async def apply(
        self,
        operations: list[dict[str, Any]],
        idempotency_key: str,
    ) -> ConnectorVersion: ...

    async def verify(self, expected: list[dict[str, Any]]) -> list[bool]: ...


class ConnectorNotConfigured(RuntimeError):
    pass


class ConnectorMutationNotImplemented(RuntimeError):
    pass


class ConnectorReadError(ValueError):
    def __init__(self, issues: tuple[object, ...]) -> None:
        super().__init__("connector input failed validation")
        self.issues = issues

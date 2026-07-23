"""Safe server-configured connector primitives shared by API and database adapters.

This module intentionally contains no DSN, arbitrary SQL, or credential values.  Concrete
stores receive only an already-authorized server-side configuration and expose bounded records
and allow-listed mutations through this contract.
"""

import json
import re
from collections.abc import AsyncIterator, Mapping
from hashlib import sha256
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Table, and_, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.base import ConnectorVersion

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MUTATIONS = frozenset({"create", "update", "delete"})


class ConnectorCapabilityError(RuntimeError):
    """Raised when a connector does not expose a requested safe capability."""


class ConnectorConflictError(RuntimeError):
    """Raised when a target version or operation precondition is stale."""


class ConnectorCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    read: bool = True
    paginated: bool = False
    streaming: bool = False
    create: bool = False
    update: bool = False
    delete: bool = False
    optimistic_version: bool = False
    read_after_write: bool = True

    def allows(self, operation: str) -> bool:
        return bool(getattr(self, operation, False)) if operation in _MUTATIONS else False


class ConnectorHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ready: bool
    capability_summary: ConnectorCapabilities
    detail_code: str


class ConnectorPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor: str | None
    records: tuple[dict[str, object], ...]
    next_cursor: str | None


class ConnectorSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: tuple[str, ...]


class ConnectorConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    credential_reference: str = Field(min_length=1, max_length=512)
    record_id_field: str
    version_field: str
    source_role: Literal["authoritative", "target"] = "target"
    capabilities: ConnectorCapabilities = Field(default_factory=ConnectorCapabilities)

    @field_validator("credential_reference")
    @classmethod
    def _credential_reference_is_not_a_secret(cls, value: str) -> str:
        if "://" in value and not value.startswith("secret://"):
            raise ValueError("credential_reference must be a server-side secret reference")
        if any(character.isspace() for character in value):
            raise ValueError("credential_reference must not contain whitespace")
        return str(value)

    @field_validator("record_id_field", "version_field")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("connector identifier is invalid")
        return str(value)


class ApiConnectorConfiguration(ConnectorConfiguration):
    endpoint: str = Field(pattern=r"^https?://[^\s]+$")

    @field_validator("endpoint")
    @classmethod
    def _endpoint_has_no_embedded_credentials(cls, value: str) -> str:
        authority = value.split("//", 1)[1].split("/", 1)[0]
        if "@" in authority:
            raise ValueError("endpoint must not contain credentials")
        return value


class DatabaseConnectorConfiguration(ConnectorConfiguration):
    table_name: str
    primary_key: str
    version_column: str
    field_columns: dict[str, str]

    @model_validator(mode="before")
    @classmethod
    def _derive_connector_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        configured = dict(value)
        configured.setdefault("record_id_field", configured.get("primary_key"))
        configured.setdefault("version_field", configured.get("version_column"))
        return configured

    @field_validator("table_name", "primary_key", "version_column")
    @classmethod
    def _safe_database_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("database connector identifier is invalid")
        return value

    @field_validator("field_columns")
    @classmethod
    def _safe_field_columns(cls, value: dict[str, str]) -> dict[str, str]:
        if not value or any(not _IDENTIFIER.fullmatch(column) for column in value.values()):
            raise ValueError("database connector identifier is invalid")
        return value

    @model_validator(mode="after")
    def _database_fields_are_consistent(self) -> "DatabaseConnectorConfiguration":
        if self.record_id_field != self.primary_key or self.version_field != self.version_column:
            raise ValueError("database identifier and version fields must match configured columns")
        return self


class ConnectorStore(Protocol):
    async def health(self) -> bool: ...

    async def version(self, version_field: str) -> str: ...

    async def page(
        self, *, cursor: str | None, page_size: int, record_id_field: str
    ) -> ConnectorPage: ...

    async def mutate(
        self,
        *,
        operations: list[dict[str, object]],
        idempotency_key: str,
        expected_version: str,
        record_id_field: str,
        version_field: str,
    ) -> str: ...

    async def verify(
        self, *, expected: list[dict[str, object]], record_id_field: str
    ) -> list[bool]: ...

    async def record(
        self, *, identifier: str, record_id_field: str
    ) -> dict[str, object] | None: ...

    async def schema(self) -> ConnectorSchema: ...


class CredentialResolver(Protocol):
    def resolve(self, reference: str) -> str: ...


class StaticCredentialResolver:
    """Small server-side resolver used by tests and explicit local configuration."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def resolve(self, reference: str) -> str:
        try:
            return self._values[reference]
        except KeyError as error:
            raise ConnectorCapabilityError(
                "connector credential reference is unavailable"
            ) from error


class HttpJsonConnectorStore:
    """Bounded JSON-over-HTTP adapter; URLs and credentials stay server-side."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        configuration: ApiConnectorConfiguration,
        credentials: CredentialResolver,
    ) -> None:
        self._client = client
        self.configuration = configuration
        self._credentials = credentials

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._credentials.resolve(self.configuration.credential_reference)}

    async def health(self) -> bool:
        try:
            response = await self._client.head(self.configuration.endpoint, headers=self._headers())
        except httpx.HTTPError:
            return False
        return response.is_success

    async def version(self, version_field: str) -> str:
        try:
            response = await self._client.head(self.configuration.endpoint, headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ConnectorCapabilityError("connector API version check failed") from error
        value = response.headers.get("etag") or response.headers.get("x-target-version")
        if not value:
            raise ConnectorCapabilityError("connector API did not provide a version token")
        return str(value)

    async def schema(self) -> ConnectorSchema:
        endpoint = f"{self.configuration.endpoint.rstrip('/')}/schema"
        try:
            response = await self._client.get(endpoint, headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ConnectorCapabilityError("connector API schema discovery failed") from error
        payload = response.json()
        fields = payload.get("fields") if isinstance(payload, dict) else None
        if not isinstance(fields, list) or any(not isinstance(field, str) for field in fields):
            raise ConnectorCapabilityError("connector API returned an invalid schema")
        return ConnectorSchema(fields=tuple(sorted(set(fields))))

    async def page(
        self, *, cursor: str | None, page_size: int, record_id_field: str
    ) -> ConnectorPage:
        try:
            params: dict[str, str | int] = {"limit": page_size}
            if cursor is not None:
                params["cursor"] = cursor
            response = await self._client.get(
                self.configuration.endpoint,
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ConnectorCapabilityError("connector API page read failed") from error
        payload = response.json()
        if not isinstance(payload, dict):
            raise ConnectorCapabilityError("connector API returned an invalid page")
        records = payload.get("items")
        response_cursor = payload.get("cursor")
        next_cursor = payload.get("next_cursor")
        if (
            not isinstance(records, list)
            or response_cursor != cursor
            or next_cursor is not None
            and not isinstance(next_cursor, str)
            or any(not isinstance(record, dict) for record in records)
        ):
            raise ConnectorCapabilityError("connector API returned an invalid page")
        identifiers = [str(record.get(record_id_field, "")) for record in records]
        if not all(identifiers) or identifiers != sorted(identifiers):
            raise ConnectorCapabilityError("connector API page has no stable record ordering")
        return ConnectorPage(
            cursor=cursor,
            records=tuple(dict(record) for record in records),
            next_cursor=next_cursor,
        )

    async def mutate(
        self,
        *,
        operations: list[dict[str, object]],
        idempotency_key: str,
        expected_version: str,
        record_id_field: str,
        version_field: str,
    ) -> str:
        endpoint = f"{self.configuration.endpoint.rstrip('/')}/mutations"
        try:
            response = await self._client.post(
                endpoint,
                headers={
                    **self._headers(),
                    "Idempotency-Key": idempotency_key,
                    "If-Match": expected_version,
                },
                json={"operations": operations},
            )
            if response.status_code in {409, 412}:
                raise ConnectorConflictError("connector API target version is stale")
            response.raise_for_status()
        except ConnectorConflictError:
            raise
        except httpx.HTTPError as error:
            raise ConnectorCapabilityError("connector API mutation failed") from error
        value = response.headers.get("etag") or response.headers.get("x-target-version")
        if not value:
            raise ConnectorCapabilityError("connector API mutation omitted output version")
        return str(value)

    async def verify(
        self, *, expected: list[dict[str, object]], record_id_field: str
    ) -> list[bool]:
        actual: dict[str, dict[str, object]] = {}
        async for page in self._iter_pages(record_id_field):
            actual.update({str(row[record_id_field]): row for row in page.records})
        return [
            (row := actual.get(str(item.get("id")))) is not None
            and all(row.get(key) == value for key, value in _mapping(item, "after").items())
            for item in expected
        ]

    async def record(self, *, identifier: str, record_id_field: str) -> dict[str, object] | None:
        async for page in self._iter_pages(record_id_field):
            for record in page.records:
                if str(record[record_id_field]) == identifier:
                    return record
        return None

    async def _iter_pages(self, record_id_field: str) -> AsyncIterator[ConnectorPage]:
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            page = await self.page(cursor=cursor, page_size=100, record_id_field=record_id_field)
            yield page
            if page.next_cursor is None:
                return
            if page.next_cursor in seen:
                raise ConnectorConflictError("connector pagination cursor repeated")
            seen.add(page.next_cursor)
            cursor = page.next_cursor


class SqlAlchemyConnectorStore:
    """Server-bound SQLAlchemy adapter over one predeclared table and allow-listed columns."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        table: Table,
        configuration: DatabaseConnectorConfiguration,
    ) -> None:
        allowed_columns = {
            configuration.primary_key,
            configuration.version_column,
            *configuration.field_columns.values(),
        }
        if table.name != configuration.table_name or not allowed_columns.issubset(table.c.keys()):
            raise ValueError("database connector table does not match its server configuration")
        self._engine = engine
        self._table = table
        self._configuration = configuration
        self._idempotent_versions: dict[str, str] = {}

    async def health(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(
                    select(self._table.c[self._configuration.primary_key]).limit(1)
                )
        except Exception:
            return False
        return True

    async def version(self, version_field: str) -> str:
        async with self._engine.connect() as connection:
            value = await connection.scalar(select(func.max(self._table.c[version_field])))
        return str(value) if value is not None else "empty"

    async def schema(self) -> ConnectorSchema:
        return ConnectorSchema(fields=tuple(sorted(column.name for column in self._table.columns)))

    async def page(
        self, *, cursor: str | None, page_size: int, record_id_field: str
    ) -> ConnectorPage:
        offset = int(cursor) if cursor is not None else 0
        if offset < 0:
            raise ConnectorConflictError("database connector cursor is invalid")
        statement = (
            select(self._table)
            .order_by(self._table.c[record_id_field])
            .offset(offset)
            .limit(page_size + 1)
        )
        async with self._engine.connect() as connection:
            rows = [dict(row) for row in (await connection.execute(statement)).mappings().all()]
        next_cursor = str(offset + page_size) if len(rows) > page_size else None
        return ConnectorPage(
            cursor=cursor,
            records=tuple(rows[:page_size]),
            next_cursor=next_cursor,
        )

    async def mutate(
        self,
        *,
        operations: list[dict[str, object]],
        idempotency_key: str,
        expected_version: str,
        record_id_field: str,
        version_field: str,
    ) -> str:
        prior = self._idempotent_versions.get(idempotency_key)
        if prior is not None:
            return prior
        allowed = set(self._configuration.field_columns)
        output = f"z:{sha256(f'{expected_version}:{idempotency_key}'.encode()).hexdigest()}"
        async with self._engine.begin() as connection:
            current = await connection.scalar(select(func.max(self._table.c[version_field])))
            current_value = str(current) if current is not None else "empty"
            if current_value != expected_version:
                raise ConnectorConflictError("database connector target version is stale")
            for operation in operations:
                kind = operation.get("operation")
                identifier = operation.get("id")
                if not isinstance(kind, str) or kind not in _MUTATIONS or identifier is None:
                    raise ConnectorCapabilityError("database connector operation is invalid")
                before = _mapping(operation, "before")
                after = _mapping(operation, "after")
                if set(before).union(after).difference(allowed):
                    raise ConnectorCapabilityError("database connector field is not allow-listed")
                predicate = [self._table.c[record_id_field] == identifier]
                predicate.extend(self._table.c[field] == value for field, value in before.items())
                if kind == "update":
                    result = await connection.execute(
                        update(self._table)
                        .where(and_(*predicate))
                        .values(**after, **{version_field: output})
                    )
                    if result.rowcount != 1:
                        raise ConnectorConflictError("database connector before value is stale")
                elif kind == "delete":
                    result = await connection.execute(delete(self._table).where(and_(*predicate)))
                    if result.rowcount != 1:
                        raise ConnectorConflictError("database connector before value is stale")
                elif kind == "create":
                    values = {record_id_field: identifier, version_field: output, **after}
                    try:
                        await connection.execute(insert(self._table).values(**values))
                    except Exception as error:
                        raise ConnectorConflictError(
                            "database connector record already exists"
                        ) from error
        self._idempotent_versions[idempotency_key] = output
        return output

    async def verify(
        self, *, expected: list[dict[str, object]], record_id_field: str
    ) -> list[bool]:
        outcomes: list[bool] = []
        async with self._engine.connect() as connection:
            for item in expected:
                identifier = item.get("id")
                row = await connection.execute(
                    select(self._table).where(self._table.c[record_id_field] == identifier)
                )
                actual = row.mappings().first()
                outcomes.append(
                    actual is not None
                    and all(
                        actual.get(key) == value for key, value in _mapping(item, "after").items()
                    )
                )
        return outcomes

    async def record(self, *, identifier: str, record_id_field: str) -> dict[str, object] | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                select(self._table).where(self._table.c[record_id_field] == identifier)
            )
            row = result.mappings().first()
        return dict(row) if row is not None else None


class ConfiguredApiConnector:
    """Capability-gated connector façade for a server-provisioned API/database store."""

    def __init__(self, *, configuration: ConnectorConfiguration, store: ConnectorStore) -> None:
        self.configuration = configuration
        self._store = store

    async def health(self) -> ConnectorHealth:
        ready = await self._store.health()
        return ConnectorHealth(
            ready=ready,
            capability_summary=self.configuration.capabilities,
            detail_code="ready" if ready else "connector_unavailable",
        )

    async def version(self) -> ConnectorVersion:
        self._require_read()
        return ConnectorVersion(value=await self._store.version(self.configuration.version_field))

    async def discover_schema(self) -> ConnectorSchema:
        self._require_read()
        return await self._store.schema()

    async def read_pages(self, *, page_size: int = 100) -> AsyncIterator[ConnectorPage]:
        self._require_read()
        if page_size < 1 or page_size > 1000:
            raise ValueError("page_size must be between 1 and 1000")
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = await self._store.page(
                cursor=cursor,
                page_size=page_size,
                record_id_field=self.configuration.record_id_field,
            )
            if page.cursor != cursor:
                raise ConnectorConflictError("connector returned an inconsistent page cursor")
            yield page
            if page.next_cursor is None:
                return
            if page.next_cursor in seen_cursors:
                raise ConnectorConflictError("connector pagination cursor repeated")
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor

    async def apply(
        self,
        operations: list[dict[str, object]],
        *,
        idempotency_key: str,
        expected_version: str,
    ) -> ConnectorVersion:
        if self.configuration.source_role != "target":
            raise ConnectorCapabilityError("authoritative connectors are read-only")
        if not self.configuration.capabilities.optimistic_version:
            raise ConnectorCapabilityError("connector lacks optimistic version capability")
        if not idempotency_key.strip() or not expected_version.strip():
            raise ValueError("idempotency_key and expected_version are required")
        for operation in operations:
            kind = operation.get("operation")
            if not isinstance(kind, str) or not self.configuration.capabilities.allows(kind):
                raise ConnectorCapabilityError("connector does not support requested operation")
        output = await self._store.mutate(
            operations=operations,
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            record_id_field=self.configuration.record_id_field,
            version_field=self.configuration.version_field,
        )
        return ConnectorVersion(value=output)

    async def verify(self, expected: list[dict[str, object]]) -> list[bool]:
        self._require_read()
        return await self._store.verify(
            expected=expected,
            record_id_field=self.configuration.record_id_field,
        )

    async def read_record(self, identifier: str) -> dict[str, object] | None:
        self._require_read()
        return await self._store.record(
            identifier=identifier,
            record_id_field=self.configuration.record_id_field,
        )

    def _require_read(self) -> None:
        if not self.configuration.capabilities.read:
            raise ConnectorCapabilityError("connector lacks read capability")


class InMemoryConnectorStore:
    """Deterministic store used by connector contract tests and local synthetic verification."""

    def __init__(self, *, records: list[dict[str, object]], credential: str | None = None) -> None:
        self._records = [dict(record) for record in records]
        self._credential = credential
        self._idempotent_versions: dict[str, str] = {}

    async def health(self) -> bool:
        return True

    async def version(self, version_field: str) -> str:
        values = [str(record.get(version_field, "")) for record in self._records]
        return max(values, default="empty")

    async def schema(self) -> ConnectorSchema:
        return ConnectorSchema(
            fields=tuple(sorted({str(key) for record in self._records for key in record}))
        )

    async def page(
        self, *, cursor: str | None, page_size: int, record_id_field: str
    ) -> ConnectorPage:
        ordered = sorted(self._records, key=lambda row: str(row.get(record_id_field, "")))
        start = int(cursor) if cursor is not None else 0
        records = tuple(ordered[start : start + page_size])
        next_cursor = str(start + page_size) if start + page_size < len(ordered) else None
        return ConnectorPage(cursor=cursor, records=records, next_cursor=next_cursor)

    async def mutate(
        self,
        *,
        operations: list[dict[str, object]],
        idempotency_key: str,
        expected_version: str,
        record_id_field: str,
        version_field: str,
    ) -> str:
        if idempotency_key in self._idempotent_versions:
            return self._idempotent_versions[idempotency_key]
        if await self.version(version_field) != expected_version:
            raise ConnectorConflictError("connector target version is stale")
        by_id = {str(record.get(record_id_field)): record for record in self._records}
        for operation in operations:
            kind = operation["operation"]
            identifier = str(operation["id"])
            if kind == "update":
                record = by_id.get(identifier)
                if record is None or any(
                    record.get(key) != value for key, value in _mapping(operation, "before").items()
                ):
                    raise ConnectorConflictError("connector before value is stale")
                record.update(_mapping(operation, "after"))
            elif kind == "create":
                if identifier in by_id:
                    raise ConnectorConflictError("connector record already exists")
                record = {record_id_field: identifier, **_mapping(operation, "after")}
                self._records.append(record)
                by_id[identifier] = record
            elif kind == "delete":
                record = by_id.get(identifier)
                if record is None:
                    raise ConnectorConflictError("connector record is absent")
                self._records.remove(record)
                by_id.pop(identifier)
            else:
                raise ConnectorCapabilityError("connector operation is not allow-listed")
        output = sha256(json.dumps(self._records, sort_keys=True, default=str).encode()).hexdigest()
        for record in self._records:
            record[version_field] = output
        self._idempotent_versions[idempotency_key] = output
        return output

    async def verify(
        self, *, expected: list[dict[str, object]], record_id_field: str
    ) -> list[bool]:
        by_id = {str(record.get(record_id_field)): record for record in self._records}
        return [
            (record := by_id.get(str(item.get("id")))) is not None
            and all(record.get(key) == value for key, value in _mapping(item, "after").items())
            for item in expected
        ]

    async def record(self, *, identifier: str, record_id_field: str) -> dict[str, object] | None:
        for record in self._records:
            if str(record.get(record_id_field)) == identifier:
                return dict(record)
        return None


def _mapping(value: Mapping[str, object], key: str) -> dict[str, object]:
    candidate = value.get(key, {})
    if not isinstance(candidate, Mapping):
        raise ValueError(f"connector {key} must be an object")
    return {str(field): item for field, item in candidate.items()}

"""Safe server-configured connector primitives shared by API and database adapters.

This module intentionally contains no DSN, arbitrary SQL, or credential values.  Concrete
stores receive only an already-authorized server-side configuration and expose bounded records
and allow-listed mutations through this contract.
"""

import re
import time
from collections.abc import AsyncIterator, Mapping
from hashlib import sha256
from typing import Any, Literal, Protocol

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
    field_types: dict[str, str] = Field(default_factory=dict)
    nullable_fields: tuple[str, ...] = ()


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


class DatabaseMappingConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["explicit", "llm"] = "explicit"


class DatabaseConnectorConfiguration(ConnectorConfiguration):
    dialect: Literal["mysql", "postgresql"] = "mysql"
    database_name: str | None = None
    schema_name: str | None = None
    table_name: str
    primary_key: str
    version_column: str
    mapping: DatabaseMappingConfiguration = Field(default_factory=DatabaseMappingConfiguration)
    field_columns: dict[str, str] = Field(default_factory=dict)
    allowed_columns: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _derive_connector_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        configured = dict(value)
        configured.setdefault("record_id_field", configured.get("primary_key"))
        configured.setdefault("version_field", configured.get("version_column"))
        mapping = configured.get("mapping")
        mapping_mode = mapping.get("mode", "explicit") if isinstance(mapping, dict) else "explicit"
        if "allowed_columns" not in configured and mapping_mode != "llm":
            configured["allowed_columns"] = tuple(
                sorted(
                    {
                        configured.get("primary_key"),
                        configured.get("version_column"),
                        *dict(configured.get("field_columns") or {}).values(),
                    }
                    - {None}
                )
            )
        return configured

    @field_validator(
        "database_name",
        "schema_name",
        "table_name",
        "primary_key",
        "version_column",
    )
    @classmethod
    def _safe_database_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("database connector identifier is invalid")
        return value

    @field_validator("field_columns")
    @classmethod
    def _safe_field_columns(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not _IDENTIFIER.fullmatch(column) for column in value.values()):
            raise ValueError("database connector identifier is invalid")
        return value

    @field_validator("allowed_columns")
    @classmethod
    def _safe_allowed_columns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            not value
            or len(set(value)) != len(value)
            or any(not _IDENTIFIER.fullmatch(column) for column in value)
        ):
            raise ValueError("database connector allowed columns are invalid")
        return tuple(value)

    @model_validator(mode="after")
    def _database_fields_are_consistent(self) -> "DatabaseConnectorConfiguration":
        if self.record_id_field != self.primary_key or self.version_field != self.version_column:
            raise ValueError("database identifier and version fields must match configured columns")
        if self.mapping.mode == "llm":
            return self
        required_columns = {
            self.primary_key,
            self.version_column,
            *self.field_columns.values(),
        }
        if not required_columns <= set(self.allowed_columns):
            raise ValueError("database field mapping exceeds its readable column allow-list")
        return self


class ConnectorStore(Protocol):
    async def health(self) -> bool: ...

    async def version(self, version_field: str) -> str: ...

    async def page(
        self,
        *,
        cursor: str | None,
        page_size: int,
        record_id_field: str,
        fields: tuple[str, ...] | None = None,
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


class SqlAlchemyConnectorStore:
    """Server-bound SQLAlchemy adapter over one predeclared table and allow-listed columns."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        table: Table,
        configuration: DatabaseConnectorConfiguration,
    ) -> None:
        allowed_columns = set(configuration.allowed_columns)
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
        columns = tuple(
            sorted(
                (
                    self._table.c[column_name]
                    for column_name in self._configuration.allowed_columns
                ),
                key=lambda column: column.name,
            )
        )
        return ConnectorSchema(
            fields=tuple(column.name for column in columns),
            field_types={column.name: str(column.type) for column in columns},
            nullable_fields=tuple(column.name for column in columns if column.nullable),
        )

    async def page(
        self,
        *,
        cursor: str | None,
        page_size: int,
        record_id_field: str,
        fields: tuple[str, ...] | None = None,
    ) -> ConnectorPage:
        offset = int(cursor) if cursor is not None else 0
        if offset < 0:
            raise ConnectorConflictError("database connector cursor is invalid")
        selected_fields = fields or self._configuration.allowed_columns
        if not set(selected_fields) <= set(self._configuration.allowed_columns):
            raise ConnectorCapabilityError(
                "database connector read references unavailable fields"
            )
        statement = (
            select(*(self._table.c[field] for field in selected_fields))
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
        mutation_version = _next_database_version(expected_version, idempotency_key)
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
                physical_before = {
                    self._configuration.field_columns[field]: value
                    for field, value in before.items()
                }
                physical_after = {
                    self._configuration.field_columns[field]: value
                    for field, value in after.items()
                }
                predicate = [self._table.c[record_id_field] == identifier]
                predicate.extend(
                    self._table.c[field] == value for field, value in physical_before.items()
                )
                if kind == "update":
                    result = await connection.execute(
                        update(self._table)
                        .where(and_(*predicate))
                        .values(**physical_after, **{version_field: mutation_version})
                    )
                    if result.rowcount != 1:
                        raise ConnectorConflictError("database connector before value is stale")
                elif kind == "delete":
                    result = await connection.execute(delete(self._table).where(and_(*predicate)))
                    if result.rowcount != 1:
                        raise ConnectorConflictError("database connector before value is stale")
                elif kind == "create":
                    values = {
                        record_id_field: identifier,
                        version_field: mutation_version,
                        **physical_after,
                    }
                    try:
                        await connection.execute(insert(self._table).values(**values))
                    except Exception as error:
                        raise ConnectorConflictError(
                            "database connector record already exists"
                        ) from error
            current = await connection.scalar(select(func.max(self._table.c[version_field])))
            output_version = str(current) if current is not None else "empty"
        self._idempotent_versions[idempotency_key] = output_version
        return output_version

    async def verify(
        self, *, expected: list[dict[str, object]], record_id_field: str
    ) -> list[bool]:
        outcomes: list[bool] = []
        async with self._engine.connect() as connection:
            for item in expected:
                identifier = item.get("id")
                row = await connection.execute(
                    select(
                        *(
                            self._table.c[column]
                            for column in self._configuration.allowed_columns
                        )
                    ).where(self._table.c[record_id_field] == identifier)
                )
                actual = row.mappings().first()
                after = _mapping(item, "after")
                outcomes.append(
                    actual is not None
                    and all(
                        actual.get(self._configuration.field_columns[key]) == value
                        for key, value in after.items()
                    )
                )
        return outcomes

    async def record(self, *, identifier: str, record_id_field: str) -> dict[str, object] | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                select(
                    *(
                        self._table.c[column]
                        for column in self._configuration.allowed_columns
                    )
                ).where(self._table.c[record_id_field] == identifier)
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

    async def read_pages(
        self,
        *,
        page_size: int = 100,
        fields: tuple[str, ...] | None = None,
    ) -> AsyncIterator[ConnectorPage]:
        self._require_read()
        if page_size < 1 or page_size > 1000:
            raise ValueError("page_size must be between 1 and 1000")
        cursor: str | None = None
        seen_cursors: set[str] = set()
        selected_fields = tuple(
            dict.fromkeys(
                (
                    self.configuration.record_id_field,
                    self.configuration.version_field,
                    *(fields or ()),
                )
            )
        )
        while True:
            page = await self._store.page(
                cursor=cursor,
                page_size=page_size,
                record_id_field=self.configuration.record_id_field,
                fields=selected_fields,
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


def _mapping(value: Mapping[str, object], key: str) -> dict[str, object]:
    candidate = value.get(key, {})
    if not isinstance(candidate, Mapping):
        raise ValueError(f"connector {key} must be an object")
    return {str(field): item for field, item in candidate.items()}


def _next_database_version(expected_version: str, idempotency_key: str) -> str:
    """Build a bounded token that sorts after every version issued by this adapter."""

    prior_counter = 0
    if expected_version.startswith("z~") and len(expected_version) >= 15:
        try:
            prior_counter = int(expected_version[2:15], 36)
        except ValueError:
            prior_counter = 0
    counter = max(time.time_ns(), prior_counter + 1)
    encoded_counter = _base36(counter).rjust(13, "0")
    digest = sha256(f"{expected_version}:{idempotency_key}".encode()).hexdigest()
    return f"z~{encoded_counter}{digest[:49]}"


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    encoded = ""
    while value:
        value, remainder = divmod(value, 36)
        encoded = alphabet[remainder] + encoded
    return encoded or "0"

import json
from collections.abc import Mapping
from hashlib import sha256

from app.connectors.configured import (
    CANONICAL_DATABASE_MAPPING_FIELDS,
    ConnectorCapabilityError,
    ConnectorColumnSchema,
    ConnectorConflictError,
    ConnectorPage,
    ConnectorSchema,
)


class InMemoryConnectorStore:
    """Deterministic connector store for tests."""

    def __init__(self, *, records: list[dict[str, object]]) -> None:
        self._records = [dict(record) for record in records]
        self._idempotent_versions: dict[str, str] = {}
        self._current_version: str | None = None
        self._field_columns: dict[str, str] | None = None
        self._effective_columns: tuple[str, ...] | None = None

    async def health(self) -> bool:
        return True

    async def version(self, version_field: str) -> str:
        if self._current_version is None:
            self._current_version = max(
                (str(record.get(version_field, "")) for record in self._records),
                default="empty",
            )
        return self._current_version

    async def schema(self) -> ConnectorSchema:
        fields = tuple(sorted({str(key) for record in self._records for key in record}))
        return ConnectorSchema(
            fields=fields,
            columns=tuple(
                ConnectorColumnSchema(
                    name=field,
                    sql_type="unknown",
                    nullable=True,
                    primary_key=False,
                    generated=False,
                    autoincrement=False,
                )
                for field in fields
            ),
        )

    def with_frozen_mapping(
        self,
        mapping: Mapping[str, str],
        *,
        record_id_field: str,
        version_field: str,
    ) -> "InMemoryConnectorStore":
        frozen_mapping = dict(mapping)
        if not set(frozen_mapping) <= CANONICAL_DATABASE_MAPPING_FIELDS:
            raise ConnectorCapabilityError("database connector mapping uses non-canonical fields")
        physical_columns = tuple(frozen_mapping.values())
        available_columns = {str(key) for record in self._records for key in record}
        if any(
            not isinstance(column, str) or column not in available_columns
            for column in physical_columns
        ):
            raise ConnectorCapabilityError(
                "database connector mapping references unavailable columns"
            )
        if len(set(physical_columns)) != len(physical_columns):
            raise ConnectorCapabilityError(
                "database connector mapping references duplicate columns"
            )
        frozen = InMemoryConnectorStore(records=[])
        frozen._records = self._records
        frozen._idempotent_versions = self._idempotent_versions
        frozen._current_version = self._current_version
        frozen._field_columns = frozen_mapping
        frozen._effective_columns = tuple(
            dict.fromkeys((record_id_field, version_field, *physical_columns))
        )
        return frozen

    async def page(
        self,
        *,
        cursor: str | None,
        page_size: int,
        record_id_field: str,
        fields: tuple[str, ...] | None = None,
    ) -> ConnectorPage:
        ordered = sorted(self._records, key=lambda row: str(row.get(record_id_field, "")))
        start = int(cursor) if cursor is not None else 0
        selected_fields = fields or self._effective_columns or ()
        if self._effective_columns is not None and not set(selected_fields) <= set(
            self._effective_columns
        ):
            raise ConnectorCapabilityError("database connector read references unavailable fields")
        selected = set(selected_fields)
        records = tuple(
            {
                key: value
                for key, value in row.items()
                if not selected or key in selected
            }
            for row in ordered[start : start + page_size]
        )
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
            before = self._physical_mapping(operation, "before")
            after = self._physical_mapping(operation, "after")
            if kind == "update":
                record = by_id.get(identifier)
                if record is None or any(
                    record.get(key) != value
                    for key, value in before.items()
                ):
                    raise ConnectorConflictError("connector before value is stale")
                record.update(after)
            elif kind == "create":
                if identifier in by_id:
                    raise ConnectorConflictError("connector record already exists")
                record = {record_id_field: identifier, **after}
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
        output = sha256(
            json.dumps(self._records, sort_keys=True, default=str).encode()
        ).hexdigest()
        for record in self._records:
            record[version_field] = output
        self._current_version = output
        self._idempotent_versions[idempotency_key] = output
        return output

    async def verify(
        self, *, expected: list[dict[str, object]], record_id_field: str
    ) -> list[bool]:
        by_id = {str(record.get(record_id_field)): record for record in self._records}
        outcomes: list[bool] = []
        for item in expected:
            record = by_id.get(str(item.get("id")))
            after = self._physical_mapping(item, "after")
            outcomes.append(
                record is not None
                and all(record.get(key) == value for key, value in after.items())
            )
        return outcomes

    async def record(
        self, *, identifier: str, record_id_field: str
    ) -> dict[str, object] | None:
        for record in self._records:
            if str(record.get(record_id_field)) == identifier:
                if self._effective_columns is None:
                    return dict(record)
                return {
                    key: value
                    for key, value in record.items()
                    if key in self._effective_columns
                }
        return None

    def _physical_mapping(self, value: Mapping[str, object], key: str) -> dict[str, object]:
        fields = _mapping(value, key)
        if self._field_columns is None:
            return fields
        if not set(fields) <= set(self._field_columns):
            raise ConnectorCapabilityError("database connector field is not allow-listed")
        return {self._field_columns[field]: item for field, item in fields.items()}


def _mapping(value: Mapping[str, object], key: str) -> dict[str, object]:
    candidate = value.get(key, {})
    if not isinstance(candidate, Mapping):
        raise ValueError(f"connector {key} must be an object")
    return {str(field): item for field, item in candidate.items()}

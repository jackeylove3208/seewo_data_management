import asyncio
import csv
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.schemas.canonical_entities import EntityType
from app.schemas.executions import GovernanceOperation, OperationType, json_values_equal


class CsvMutationError(ValueError):
    pass


class TargetVersionLike(Protocol):
    id: UUID
    task_id: UUID
    tenant_id: str
    source_snapshot_id: UUID
    storage_path: str
    file_sha256: str


class TargetVersionRepository(Protocol):
    async def create_target_version(
        self,
        *,
        task_id: UUID,
        tenant_id: str,
        source_snapshot_id: UUID,
        parent_version_id: UUID | None,
        batch_id: UUID | None,
        file_sha256: str,
        content_hash: str,
        storage_path: str | Path,
    ) -> Any: ...


_ENTITY_TYPE_EXPORT = {
    EntityType.ORGANIZATION_UNIT: "部门",
    EntityType.CLASS: "班级",
    EntityType.TEACHER: "教师",
    EntityType.STUDENT: "学生",
    EntityType.MEMBERSHIP: "关系",
}

_COLUMN_NAMES = {
    "source_id": "id",
    "parent_source_id": "parent_id",
    "department_source_id": "parent_id",
    "class_source_id": "parent_id",
    "member_source_id": "member_id",
    "container_source_id": "container_id",
}

_AGENT_COLUMN_ALIASES = {
    "category": ("category", "类别", "entity_type", "实体类型"),
    "name": ("name", "姓名", "名称"),
    "number": ("number", "编号", "工号", "学号"),
    "class_name": ("class_name", "class", "班级"),
    "phone": ("phone", "电话", "手机号"),
    "email": ("email", "邮箱", "电子邮箱"),
}


class CsvTargetVersioner:
    def __init__(
        self,
        *,
        repository: TargetVersionRepository,
        output_root: Path,
    ) -> None:
        self.repository = repository
        self.output_root = output_root

    async def derive(
        self,
        parent: TargetVersionLike,
        operations: Sequence[GovernanceOperation],
        *,
        batch_id: UUID,
    ) -> Any:
        session = await self.begin(parent, batch_id=batch_id)
        for operation in operations:
            await session.apply_operation(operation)
        return await session.finalize()

    async def begin(
        self,
        parent: TargetVersionLike,
        *,
        batch_id: UUID,
    ) -> "CsvTargetMutationSession":
        fieldnames, rows = _read_csv(Path(parent.storage_path))
        return CsvTargetMutationSession(
            repository=self.repository,
            output_root=self.output_root,
            parent=parent,
            batch_id=batch_id,
            fieldnames=list(fieldnames),
            rows=rows,
        )


class CsvTargetMutationSession:
    def __init__(
        self,
        *,
        repository: TargetVersionRepository,
        output_root: Path,
        parent: TargetVersionLike,
        batch_id: UUID,
        fieldnames: list[str],
        rows: list[dict[str, str]],
    ) -> None:
        self.repository = repository
        self.output_root = output_root
        self.parent = parent
        self.batch_id = batch_id
        self.fieldnames = fieldnames
        self.rows = rows
        self._closed = False

    async def apply_operation(self, operation: GovernanceOperation) -> None:
        self._require_open()
        for field in operation.after or {}:
            column = _column_for(field, self.fieldnames)
            if column not in self.fieldnames:
                self.fieldnames.append(column)
        self.rows = apply_operation(self.rows, operation, self.fieldnames)

    async def read_entity(self, identifier: str) -> dict[str, object] | None:
        self._require_open()
        indexes = _matching_indexes(self.rows, identifier)
        if len(indexes) != 1:
            return None
        return _canonical_row(self.rows[indexes[0]])

    async def finalize(self) -> Any:
        self._require_open()
        self._closed = True
        return await _publish_rows(
            repository=self.repository,
            output_root=self.output_root,
            parent=self.parent,
            batch_id=self.batch_id,
            fieldnames=self.fieldnames,
            rows=self.rows,
        )

    async def abort(self) -> None:
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("target mutation session is closed")


async def _publish_rows(
    *,
    repository: TargetVersionRepository,
    output_root: Path,
    parent: TargetVersionLike,
    batch_id: UUID,
    fieldnames: Sequence[str],
    rows: list[dict[str, str]],
) -> Any:
    updated_fieldnames = list(fieldnames)

    await asyncio.to_thread(output_root.mkdir, parents=True, exist_ok=True)
    output_path = output_root / f"{batch_id}-{uuid4().hex}.csv"
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=output_root,
            prefix=".target-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            writer = csv.DictWriter(
                handle,
                fieldnames=updated_fieldnames,
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(
                {column: safe_csv_value(row.get(column, "")) for column in updated_fieldnames}
                for row in rows
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, output_path)
        temp_path = None
        file_sha256 = _sha256_file(output_path)
        content_hash = hashlib.sha256(
            json.dumps(
                rows,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return await repository.create_target_version(
            task_id=parent.task_id,
            tenant_id=parent.tenant_id,
            source_snapshot_id=parent.source_snapshot_id,
            parent_version_id=parent.id,
            batch_id=batch_id,
            file_sha256=file_sha256,
            content_hash=content_hash,
            storage_path=output_path,
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def apply_operation(
    rows: list[dict[str, str]],
    operation: GovernanceOperation,
    fieldnames: Sequence[str],
) -> list[dict[str, str]]:
    if operation.operation_type is OperationType.SKIP:
        return rows
    if operation.operation_type is OperationType.CREATE:
        after = operation.after or {}
        source_identifier = after.get("source_id") or operation.target_source_identifier
        if source_identifier is None:
            source_identifier = f"generated-{operation.id}"
        identifier = str(source_identifier)
        if _matching_indexes(rows, identifier):
            raise CsvMutationError(f"target row already exists: {identifier}")
        created = {column: "" for column in fieldnames}
        created["id"] = identifier
        created["entity_type"] = _ENTITY_TYPE_EXPORT[operation.entity_type]
        _apply_after(created, after)
        return [*rows, created]

    target_identifier = operation.target_source_identifier
    if target_identifier is None:
        raise CsvMutationError("target mutation requires a connector identifier")
    indexes = _matching_indexes(rows, target_identifier)
    if len(indexes) != 1:
        raise CsvMutationError(
            f"target row must resolve exactly once: {target_identifier} (found {len(indexes)})"
        )
    index = indexes[0]
    if operation.restore_absence:
        if operation.compensation_for is None:
            raise CsvMutationError("restore_absence is compensation-only")
        return [*rows[:index], *rows[index + 1 :]]
    current = rows[index]
    _require_before(current, operation.before or {})
    updated = dict(current)
    _apply_after(updated, operation.after or {})
    return [*rows[:index], updated, *rows[index + 1 :]]


def safe_csv_value(value: object) -> object:
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
    return value


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise CsvMutationError("target CSV requires a header row")
            rows = [dict(row) for row in reader]
            fieldnames = list(reader.fieldnames)
            if "id" not in fieldnames:
                fieldnames.append("id")
                for physical_row_number, row in enumerate(rows, start=2):
                    row["id"] = f"csv:{physical_row_number}"
            return tuple(fieldnames), rows
    except UnicodeDecodeError as error:
        raise CsvMutationError("target CSV must use UTF-8 for execution") from error


def read_target_rows(path: Path) -> dict[str, dict[str, object]]:
    """Return physical CSV values projected onto canonical fields by stable row ID."""
    _fieldnames, rows = _read_csv(path)
    indexed: dict[str, dict[str, object]] = {}
    for row in rows:
        identifier = row.get("id")
        if not identifier or identifier in indexed:
            raise CsvMutationError("target CSV requires unique stable row identifiers")
        indexed[identifier] = _canonical_row(row)
    return indexed


def _matching_indexes(rows: Sequence[Mapping[str, str]], identifier: str) -> list[int]:
    return [index for index, row in enumerate(rows) if row.get("id") == identifier]


def _apply_after(row: dict[str, str], after: Mapping[str, object]) -> None:
    for field, value in after.items():
        if field == "entity_type":
            continue
        row[_column_for(field, row)] = "" if value is None else str(value)


def _require_before(row: Mapping[str, str], before: Mapping[str, object]) -> None:
    mismatches = [
        field
        for field, expected in before.items()
        if not json_values_equal(row.get(_column_for(field, row)), _csv_expected(expected))
    ]
    if mismatches:
        raise CsvMutationError(f"target before value changed: {', '.join(sorted(mismatches))}")


def _csv_expected(value: object) -> object:
    return "" if value is None else str(value)


def _column_for(field: str, columns: Mapping[str, object] | Sequence[str]) -> str:
    available = set(columns)
    canonical = _COLUMN_NAMES.get(field, field)
    for candidate in _AGENT_COLUMN_ALIASES.get(canonical, (canonical,)):
        if candidate in available:
            return candidate
    return canonical


def _canonical_row(row: Mapping[str, str]) -> dict[str, object]:
    facts: dict[str, object] = dict(row)
    for canonical, aliases in _AGENT_COLUMN_ALIASES.items():
        facts[canonical] = next((row[item] for item in aliases if item in row), "")
    facts["source_id"] = row.get("id", "")
    parent = row.get("parent_id", "")
    facts["parent_source_id"] = parent
    facts["department_source_id"] = parent
    facts["class_source_id"] = parent
    facts["member_source_id"] = row.get("member_id", "")
    facts["container_source_id"] = row.get("container_id", "")
    return facts


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()

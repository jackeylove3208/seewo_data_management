# Data Source Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared backend foundation and turn one authoritative third-party CSV plus one Seewo target CSV into validated, immutable, scope-bound canonical snapshots.

**Architecture:** Implement a modular FastAPI application whose reconciliation services consume connector protocols rather than CSV files. Store original uploads, raw rows, canonical entities, mapping/schema versions, and hashes separately; a failed ingestion never publishes a usable snapshot. Every task declares tenant, scope, and whether the input is a complete or partial snapshot so later redundant detection is safe.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 16 + pgvector, Polars, charset-normalizer, pytest, Ruff, mypy, Docker Compose.

## Global Constraints

- One reconciliation task has exactly one `AUTHORITATIVE` third-party input and one `TARGET` Seewo input.
- Uploaded originals are immutable and governance output never overwrites them.
- File names stored on disk are server-generated; original names are metadata only.
- Both inputs must share `tenant_id`, `scope_id`, entity coverage, and snapshot mode.
- `FULL` snapshots may produce Seewo-redundant differences; `PARTIAL` snapshots may not.
- Canonical entities retain source role, source ID, raw row number, snapshot ID, and raw payload reference.
- CSV is the only working connector in this phase; API connectors fail explicitly with `ConnectorNotConfigured`.
- Synthetic fixtures contain no real teacher or student data and no credentials are committed.
- Duplicate source IDs, orphan references, and organization hierarchy cycles quarantine all affected rows; missing required mappings and an input with zero valid rows block snapshot publication.
- The current workspace has no Git metadata; run `git init` once at the start of Task 1 so the listed review-sized commits are executable.

---

## File Map

- `backend/app/core/`: configuration, database sessions, errors, and demo operator identity.
- `backend/app/schemas/`: Pydantic enums, canonical entities, ingestion requests, and responses.
- `backend/app/models/`: SQLAlchemy task, upload, snapshot, raw-row, and canonical-entity records.
- `backend/app/connectors/`: stable source/target protocols, registry, CSV implementations, and API stubs.
- `backend/app/ingestion/`: file inspection, field mapping, batch validation, and quarantine generation.
- `backend/app/snapshots/`: atomic immutable snapshot publication and content hashing.
- `backend/app/api/routes/`: health, upload, and reconciliation-task endpoints.
- `backend/tests/`: unit, connector-contract, integration, and synthetic fixture coverage.

### Task 1: Bootstrap the backend and local database

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/main.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/core/database.py`
- Create: `backend/app/api/routes/health.py`
- Create: `infra/docker-compose.yml`
- Create: `infra/env.example`
- Test: `backend/tests/integration/test_health.py`

**Interfaces:**
- Consumes: environment variables `DATABASE_URL`, `UPLOAD_ROOT`, `MAX_UPLOAD_BYTES`, and `DEMO_OPERATOR_ID`.
- Produces: `create_app() -> FastAPI`, `get_session() -> AsyncIterator[AsyncSession]`, `/health/live`, and `/health/ready`.

- [ ] **Step 0: Initialize local version control**

Run: `git init`

Expected: Git reports an initialized repository in `/Users/lbs/PycharmProjects/PythonProject/.git`.

- [ ] **Step 1: Add the failing health test**

```python
from fastapi.testclient import TestClient
from app.main import create_app

def test_liveness_is_process_only() -> None:
    response = TestClient(create_app()).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run the focused test**

Run: `cd backend && uv run pytest tests/integration/test_health.py -q`

Expected: FAIL because `app.main` does not exist.

- [ ] **Step 3: Add Python 3.12 dependencies and the application factory**

```toml
[project]
name = "organization-reconciliation"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.115,<1", "uvicorn[standard]>=0.34,<1", "pydantic>=2.10,<3",
  "pydantic-settings>=2.7,<3", "sqlalchemy[asyncio]>=2.0,<3",
  "asyncpg>=0.30,<1", "alembic>=1.14,<2", "polars>=1.20,<2",
  "python-multipart>=0.0.20,<1", "charset-normalizer>=3.4,<4",
  "httpx>=0.28,<1", "tenacity>=9,<10", "rapidfuzz>=3.11,<4", "pgvector>=0.3,<1"
]

[dependency-groups]
dev = ["pytest>=8.3,<9", "pytest-asyncio>=0.25,<1", "ruff>=0.9,<1", "mypy>=1.14,<2"]
```

```python
# backend/app/main.py
from fastapi import FastAPI
from app.api.routes.health import router as health_router

def create_app() -> FastAPI:
    app = FastAPI(title="Organization Reconciliation API", version="0.1.0")
    app.include_router(health_router, prefix="/health", tags=["health"])
    return app

app = create_app()
```

- [ ] **Step 4: Add typed configuration, database readiness, and Compose**

```python
# backend/app/core/config.py
from functools import lru_cache
from pathlib import Path
from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://reconcile:reconcile@localhost:5432/reconcile"
    upload_root: Path = Path("storage/uploads")
    max_upload_bytes: PositiveInt = 50 * 1024 * 1024
    demo_operator_id: str = "demo-operator"

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

```yaml
# infra/docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: reconcile
      POSTGRES_USER: reconcile
      POSTGRES_PASSWORD: reconcile
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U reconcile"]
      interval: 2s
      timeout: 2s
      retries: 20
```

- [ ] **Step 5: Verify and commit**

Run: `docker compose -f infra/docker-compose.yml up -d && cd backend && uv run pytest tests/integration/test_health.py -q && uv run ruff check . && uv run mypy app`

Expected: health test PASS; lint and types report no errors.

```bash
git add backend infra
git commit -m "feat: bootstrap ingestion backend"
```

### Task 2: Define canonical ingestion contracts

**Files:**
- Create: `backend/app/schemas/common.py`
- Create: `backend/app/schemas/canonical_entities.py`
- Create: `backend/app/schemas/ingestion.py`
- Test: `backend/tests/unit/schemas/test_canonical_entities.py`

**Interfaces:**
- Consumes: raw mapped fields and task scope metadata.
- Produces: `OrganizationUnit`, `ClassEntity`, `Teacher`, `Student`, `Membership`, `SnapshotScope`, `CanonicalBatch`, and `IngestionSummary`.

- [ ] **Step 1: Specify provenance and scope behavior in tests**

```python
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.schemas.canonical_entities import EntityType, SourceRole, Teacher
from app.schemas.ingestion import SnapshotMode, SnapshotScope

def test_teacher_requires_stable_provenance() -> None:
    teacher = Teacher(
        tenant_id="school-1", snapshot_id=uuid4(), source_role=SourceRole.AUTHORITATIVE,
        source_id="t-7", raw_row_number=8, name=" 张三 ", employee_number="E007"
    )
    assert teacher.entity_type is EntityType.TEACHER

def test_partial_scope_rejects_redundant_detection() -> None:
    scope = SnapshotScope(tenant_id="school-1", scope_id="campus-a", mode=SnapshotMode.PARTIAL)
    assert scope.allows_redundant_detection is False

def test_row_number_is_one_based() -> None:
    with pytest.raises(ValidationError):
        Teacher(tenant_id="s", snapshot_id=uuid4(), source_role="target", raw_row_number=0, name="A")
```

- [ ] **Step 2: Run the schema tests**

Run: `cd backend && uv run pytest tests/unit/schemas/test_canonical_entities.py -q`

Expected: FAIL because the canonical schemas do not exist.

- [ ] **Step 3: Implement exact shared types and entity models**

```python
# backend/app/schemas/canonical_entities.py
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

class SourceRole(StrEnum):
    AUTHORITATIVE = "authoritative"
    TARGET = "target"

class EntityType(StrEnum):
    ORGANIZATION_UNIT = "organization_unit"
    CLASS = "class"
    TEACHER = "teacher"
    STUDENT = "student"
    MEMBERSHIP = "membership"

class ProvenancedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str = Field(min_length=1)
    snapshot_id: UUID
    source_role: SourceRole
    source_id: str | None = None
    raw_row_number: int = Field(ge=1)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

class OrganizationUnit(ProvenancedEntity):
    entity_type: Literal[EntityType.ORGANIZATION_UNIT] = EntityType.ORGANIZATION_UNIT
    name: str
    code: str | None = None
    parent_source_id: str | None = None
    campus_id: str | None = None

class ClassEntity(ProvenancedEntity):
    entity_type: Literal[EntityType.CLASS] = EntityType.CLASS
    name: str
    grade: str | None = None
    school_year: str | None = None
    parent_source_id: str | None = None

class Teacher(ProvenancedEntity):
    entity_type: Literal[EntityType.TEACHER] = EntityType.TEACHER
    name: str
    employee_number: str | None = None
    phone: str | None = None
    email: str | None = None
    department_source_id: str | None = None

class Student(ProvenancedEntity):
    entity_type: Literal[EntityType.STUDENT] = EntityType.STUDENT
    name: str
    student_number: str | None = None
    class_source_id: str | None = None

class Membership(ProvenancedEntity):
    entity_type: Literal[EntityType.MEMBERSHIP] = EntityType.MEMBERSHIP
    member_source_id: str
    container_source_id: str
    role: str

CanonicalEntity = OrganizationUnit | ClassEntity | Teacher | Student | Membership
```

```python
# backend/app/schemas/ingestion.py
from enum import StrEnum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, computed_field
from app.schemas.canonical_entities import CanonicalEntity, EntityType, SourceRole

class SnapshotMode(StrEnum):
    FULL = "full"
    PARTIAL = "partial"

class SnapshotScope(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: str
    scope_id: str
    mode: SnapshotMode
    entity_types: frozenset[EntityType] = Field(default_factory=lambda: frozenset(EntityType))

    @computed_field
    @property
    def allows_redundant_detection(self) -> bool:
        return self.mode is SnapshotMode.FULL

class CanonicalBatch(BaseModel):
    snapshot_id: UUID
    source_role: SourceRole
    entities: list[CanonicalEntity]

class IngestionSummary(BaseModel):
    accepted: int = 0
    normalized_with_warning: int = 0
    quarantined: int = 0
    rejected: int = 0
```

- [ ] **Step 4: Verify and commit**

Run: `cd backend && uv run pytest tests/unit/schemas/test_canonical_entities.py -q`

Expected: 3 tests PASS.

```bash
git add backend/app/schemas backend/tests/unit/schemas
git commit -m "feat: define canonical ingestion contracts"
```

### Task 3: Persist task, upload, and immutable snapshot records

**Files:**
- Create: `backend/app/models/base.py`
- Create: `backend/app/models/reconciliation.py`
- Create: `backend/app/models/snapshots.py`
- Create: `backend/app/repositories/tasks.py`
- Create: `backend/app/repositories/snapshots.py`
- Create: `backend/alembic/versions/0001_ingestion_foundation.py`
- Test: `backend/tests/integration/repositories/test_snapshots.py`

**Interfaces:**
- Consumes: `SnapshotScope`, upload metadata, raw rows, and `CanonicalEntity` payloads.
- Produces: `TaskRepository.create(...)`, `SnapshotRepository.publish_pair(...)`, immutable snapshot IDs, and database constraints preventing duplicate roles.

- [ ] **Step 1: Write the atomic publication test**

```python
import pytest
from app.repositories.snapshots import SnapshotDraft, SnapshotRepository

@pytest.mark.asyncio
async def test_pair_is_not_published_when_one_side_fails(session) -> None:
    repo = SnapshotRepository(session)
    source = SnapshotDraft(role="authoritative", file_hash="a" * 64, entities=[])
    target = SnapshotDraft(role="target", file_hash="b" * 64, entities=None)
    with pytest.raises(ValueError, match="entities"):
        await repo.publish_pair(task_id="task-1", source=source, target=target)
    assert await repo.list_published("task-1") == []
```

- [ ] **Step 2: Run the repository test**

Run: `cd backend && uv run pytest tests/integration/repositories/test_snapshots.py -q`

Expected: FAIL because `SnapshotRepository` is undefined.

- [ ] **Step 3: Add append-only tables and constraints**

```python
# backend/app/models/snapshots.py
import uuid
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin

class SourceFile(Base, TimestampMixin):
    __tablename__ = "source_files"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"))
    source_role: Mapped[str] = mapped_column(String(32))
    original_name: Mapped[str] = mapped_column(String(255))
    storage_name: Mapped[str] = mapped_column(String(80), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (UniqueConstraint("task_id", "source_role"), CheckConstraint("size_bytes > 0"))

class Snapshot(Base, TimestampMixin):
    __tablename__ = "snapshots"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"))
    source_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_files.id"))
    source_role: Mapped[str] = mapped_column(String(32))
    schema_version: Mapped[str] = mapped_column(String(32))
    mapping_version: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="draft")
    __table_args__ = (UniqueConstraint("task_id", "source_role"),)

class CanonicalEntityRecord(Base):
    __tablename__ = "canonical_entities"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snapshots.id"))
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str | None] = mapped_column(String(255), index=True)
    raw_row_number: Mapped[int] = mapped_column()
    canonical_payload: Mapped[dict] = mapped_column(JSONB)
    raw_payload: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("snapshot_id", "entity_type", "raw_row_number"),)
```

- [ ] **Step 4: Implement one-transaction pair publication**

```python
# backend/app/repositories/snapshots.py
from dataclasses import dataclass
from collections.abc import Sequence
from sqlalchemy.ext.asyncio import AsyncSession

@dataclass(frozen=True)
class SnapshotDraft:
    role: str
    file_hash: str
    entities: Sequence[dict] | None

class SnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def publish_pair(self, task_id, source: SnapshotDraft, target: SnapshotDraft):
        if source.entities is None or target.entities is None:
            raise ValueError("entities must be validated before publication")
        async with self.session.begin():
            snapshots = [await self._insert_draft(task_id, draft) for draft in (source, target)]
            for snapshot in snapshots:
                snapshot.state = "published"
        return tuple(snapshots)
```

- [ ] **Step 5: Apply migration, verify immutability, and commit**

Run: `cd backend && uv run alembic upgrade head && uv run pytest tests/integration/repositories/test_snapshots.py -q`

Expected: migration succeeds and atomic publication test PASS.

```bash
git add backend/app/models backend/app/repositories backend/alembic backend/tests/integration/repositories
git commit -m "feat: persist immutable ingestion snapshots"
```

### Task 4: Define connector contracts and registry

**Files:**
- Create: `backend/app/connectors/base.py`
- Create: `backend/app/connectors/registry.py`
- Create: `backend/app/connectors/seewo_api.py`
- Create: `backend/app/connectors/third_party_api.py`
- Test: `backend/tests/contract/test_connector_contract.py`

**Interfaces:**
- Consumes: `ConnectorReadRequest` and `GovernanceOperation` later defined by the execution module.
- Produces: `SourceConnector.read()`, `TargetConnector.apply()`, `TargetConnector.verify()`, and `ConnectorRegistry.get()`.

- [ ] **Step 1: Write connector contract tests against a fake**

```python
from collections.abc import AsyncIterator
from app.connectors.base import ConnectorReadRequest, ConnectorVersion, SourceConnector

class FakeSource:
    async def version(self) -> ConnectorVersion:
        return ConnectorVersion(value="fixture-v1")
    async def read(self, request: ConnectorReadRequest) -> AsyncIterator[dict]:
        for row in [{"entity_type": "teacher", "name": "张三"}]:
            yield row

async def test_source_contract_emits_rows_and_version() -> None:
    connector: SourceConnector = FakeSource()
    assert (await connector.version()).value == "fixture-v1"
    assert [row async for row in connector.read(ConnectorReadRequest())][0]["name"] == "张三"
```

- [ ] **Step 2: Run the contract test**

Run: `cd backend && uv run pytest tests/contract/test_connector_contract.py -q`

Expected: FAIL because connector contracts are missing.

- [ ] **Step 3: Implement protocols, version types, and typed registry**

```python
# backend/app/connectors/base.py
from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable
from pydantic import BaseModel

class ConnectorReadRequest(BaseModel):
    entity_types: set[str] | None = None

class ConnectorVersion(BaseModel):
    value: str

@runtime_checkable
class SourceConnector(Protocol):
    async def version(self) -> ConnectorVersion: ...
    def read(self, request: ConnectorReadRequest) -> AsyncIterator[dict[str, Any]]: ...

@runtime_checkable
class TargetConnector(SourceConnector, Protocol):
    async def apply(self, operations: list[dict[str, Any]], idempotency_key: str) -> ConnectorVersion: ...
    async def verify(self, expected: list[dict[str, Any]]) -> list[bool]: ...

class ConnectorNotConfigured(RuntimeError):
    pass
```

```python
# backend/app/connectors/registry.py
from app.connectors.base import SourceConnector

class ConnectorRegistry:
    def __init__(self) -> None:
        self._items: dict[str, SourceConnector] = {}
    def register(self, name: str, connector: SourceConnector) -> None:
        self._items[name] = connector
    def get(self, name: str) -> SourceConnector:
        try:
            return self._items[name]
        except KeyError as error:
            raise LookupError(f"unknown connector: {name}") from error
```

- [ ] **Step 4: Add explicit API stubs and commit**

```python
class SeewoApiConnector:
    async def version(self):
        raise ConnectorNotConfigured("Seewo API credentials and contract are not configured")
```

Run: `cd backend && uv run pytest tests/contract/test_connector_contract.py -q`

Expected: contract test PASS and API stub test raises `ConnectorNotConfigured`.

```bash
git add backend/app/connectors backend/tests/contract
git commit -m "feat: define replaceable connector contracts"
```

### Task 5: Store and inspect uploads securely

**Files:**
- Create: `backend/app/ingestion/upload_storage.py`
- Create: `backend/app/ingestion/encoding.py`
- Create: `backend/app/ingestion/csv_reader.py`
- Test: `backend/tests/unit/ingestion/test_upload_storage.py`
- Test: `backend/tests/unit/ingestion/test_csv_reader.py`

**Interfaces:**
- Consumes: `UploadFile`, configured size limit, and upload root.
- Produces: `StoredUpload(storage_name, original_name, sha256, size_bytes)`, `CsvInspection`, stable one-based source row numbers.

- [ ] **Step 1: Add tests for traversal, size, encoding, BOM, and malformed rows**

```python
def test_storage_never_uses_client_path(storage, upload_factory) -> None:
    result = storage.save(upload_factory("../../teachers.csv", b"name\nA\n"))
    assert "teachers.csv" not in result.storage_name
    assert result.original_name == "teachers.csv"

def test_reader_reports_duplicate_headers(tmp_path) -> None:
    path = tmp_path / "bad.csv"
    path.write_bytes("name,name\nA,B\n".encode())
    result = inspect_csv(path)
    assert result.errors == ["duplicate header: name"]
```

- [ ] **Step 2: Run inspection tests**

Run: `cd backend && uv run pytest tests/unit/ingestion/test_upload_storage.py tests/unit/ingestion/test_csv_reader.py -q`

Expected: FAIL because storage and inspection functions do not exist.

- [ ] **Step 3: Implement bounded streaming storage and hash calculation**

```python
@dataclass(frozen=True)
class StoredUpload:
    storage_name: str
    original_name: str
    sha256: str
    size_bytes: int

def save_upload(stream: BinaryIO, client_name: str, root: Path, limit: int) -> StoredUpload:
    storage_name = f"{uuid4().hex}.csv"
    destination = root / storage_name
    digest, size = sha256(), 0
    with destination.open("xb") as target:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                target.close(); destination.unlink(missing_ok=True)
                raise UploadTooLarge(limit)
            digest.update(chunk); target.write(chunk)
    return StoredUpload(storage_name, Path(client_name).name, digest.hexdigest(), size)
```

- [ ] **Step 4: Implement deterministic CSV inspection**

```python
@dataclass(frozen=True)
class CsvInspection:
    encoding: str
    delimiter: str
    headers: tuple[str, ...]
    errors: tuple[str, ...]

SUPPORTED_ENCODINGS = {"utf_8", "utf_8_sig", "gb18030"}

def inspect_csv(path: Path) -> CsvInspection:
    detected = from_bytes(path.read_bytes()[:65536]).best()
    encoding = detected.encoding.lower().replace("-", "_") if detected else ""
    if encoding not in SUPPORTED_ENCODINGS:
        raise UnsupportedEncoding(encoding)
    with path.open(encoding=encoding, newline="") as handle:
        reader = csv.reader(handle)
        headers = tuple(next(reader, []))
    duplicates = sorted({h for h in headers if headers.count(h) > 1})
    errors = tuple(f"duplicate header: {h}" for h in duplicates)
    if not headers or all(not h.strip() for h in headers):
        errors += ("CSV header is empty",)
    return CsvInspection(encoding, ",", headers, errors)
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/unit/ingestion -q`

Expected: traversal, limit, encoding, BOM, duplicate-header, empty-file, and row-number tests PASS.

```bash
git add backend/app/ingestion backend/tests/unit/ingestion
git commit -m "feat: add secure csv upload inspection"
```

### Task 6: Map, validate, and quarantine CSV rows

**Files:**
- Create: `backend/app/ingestion/field_mapping.py`
- Create: `backend/app/ingestion/schema_validation.py`
- Create: `backend/app/ingestion/quarantine.py`
- Create: `docs/sample-data/field-mapping.v1.json`
- Create: `docs/sample-data/third-party.csv`
- Create: `docs/sample-data/seewo.csv`
- Test: `backend/tests/unit/ingestion/test_schema_validation.py`

**Interfaces:**
- Consumes: inspected CSV, `FieldMapping(version, entity_type_column, fields)`, and `SnapshotScope`.
- Produces: `ValidationResult(accepted, warnings, quarantined, fatal_errors, summary)`; fatal errors block all publication.

- [ ] **Step 1: Define validation behavior in tests**

```python
def test_missing_required_mapping_is_fatal(mapping_factory) -> None:
    mapping = mapping_factory(fields={"name": "姓名"})
    result = validate_batch(frame_with_teacher(), mapping, required={"teacher": {"name", "source_id"}})
    assert result.fatal_errors == ("teacher.source_id has no source column mapping",)

def test_recoverable_value_keeps_original_and_warning(valid_mapping) -> None:
    result = validate_batch(frame_with_phone("138 0000 0000"), valid_mapping)
    assert result.accepted[0]["phone"] == "13800000000"
    assert result.accepted[0]["_raw"]["手机号"] == "138 0000 0000"
    assert result.summary.normalized_with_warning == 1
```

- [ ] **Step 2: Run field mapping tests**

Run: `cd backend && uv run pytest tests/unit/ingestion/test_schema_validation.py -q`

Expected: FAIL because batch validation is missing.

- [ ] **Step 3: Implement versioned mapping and fatal schema checks**

```python
class FieldMapping(BaseModel):
    version: str
    entity_type_column: str = "entity_type"
    fields: dict[EntityType, dict[str, str]]

def missing_required_mappings(mapping: FieldMapping, required: dict[EntityType, set[str]]) -> tuple[str, ...]:
    return tuple(
        f"{entity_type.value}.{field} has no source column mapping"
        for entity_type, fields in required.items()
        for field in sorted(fields - mapping.fields.get(entity_type, {}).keys())
    )
```

- [ ] **Step 4: Implement Polars batch validation and quarantine export**

```python
@dataclass(frozen=True)
class ValidationResult:
    accepted: list[dict]
    warnings: tuple[RowIssue, ...]
    quarantined: tuple[RowIssue, ...]
    fatal_errors: tuple[str, ...]
    summary: IngestionSummary

def validate_batch(frame: pl.DataFrame, mapping: FieldMapping, required=REQUIRED_FIELDS) -> ValidationResult:
    fatal = missing_required_mappings(mapping, required)
    if fatal:
        return ValidationResult([], (), (), fatal, IngestionSummary(rejected=frame.height))
    accepted, warnings, quarantined = [], [], []
    for row_number, raw in enumerate(frame.iter_rows(named=True), start=2):
        try:
            mapped, row_warnings = map_and_normalize(raw, mapping, row_number)
            accepted.append(mapped); warnings.extend(row_warnings)
        except RowValidationError as error:
            quarantined.append(RowIssue(row_number=row_number, code=error.code, message=str(error)))
    accepted, relationship_issues = validate_relationships(accepted)
    quarantined.extend(relationship_issues)
    fatal = ("input contains zero valid rows",) if not accepted else ()
    return ValidationResult(accepted, tuple(warnings), tuple(quarantined), fatal, IngestionSummary(
        accepted=len(accepted), normalized_with_warning=len({w.row_number for w in warnings}),
        quarantined=len(quarantined), rejected=frame.height if fatal else 0,
    ))
```

```python
def validate_relationships(rows: list[dict]) -> tuple[list[dict], list[RowIssue]]:
    indexed: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("source_id"):
            indexed[(row["entity_type"], row["source_id"])].append(row)
    invalid_numbers = {
        row["_row_number"] for group in indexed.values() if len(group) > 1 for row in group
    }
    reference_fields = {
        "organization_unit": (("parent_source_id", "organization_unit"),),
        "class": (("parent_source_id", "organization_unit"),),
        "teacher": (("department_source_id", "organization_unit"),),
        "student": (("class_source_id", "class"),),
    }
    for row in rows:
        for field, target_type in reference_fields.get(row["entity_type"], ()):
            if (reference := row.get(field)) and (target_type, reference) not in indexed:
                invalid_numbers.add(row["_row_number"])
    parent_by_id = {
        row["source_id"]: row.get("parent_source_id") for row in rows
        if row["entity_type"] == "organization_unit" and row.get("source_id")
    }
    cycle_ids = find_cycle_members(parent_by_id)
    invalid_numbers.update(row["_row_number"] for row in rows if row.get("source_id") in cycle_ids)
    issues = [RowIssue(row_number=n, code="relationship_invalid", message="duplicate, orphan, or cyclic relationship")
              for n in sorted(invalid_numbers)]
    return [row for row in rows if row["_row_number"] not in invalid_numbers], issues

def find_cycle_members(parent_by_id: dict[str, str | None]) -> set[str]:
    cycle_members: set[str] = set()
    for start in parent_by_id:
        path: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        while current in parent_by_id and current not in positions:
            positions[current] = len(path); path.append(current); current = parent_by_id[current]
        if current in positions:
            cycle_members.update(path[positions[current]:])
    return cycle_members
```

- [ ] **Step 5: Add synthetic mixed-entity fixtures and commit**

The fixture header must include `entity_type,source_id,name,parent_source_id,campus_id,grade,school_year,employee_number,phone,email,student_number,member_source_id,container_source_id,role` and include all five entity types plus one quarantined row.

Run: `cd backend && uv run pytest tests/unit/ingestion/test_schema_validation.py -q`

Expected: mapping, normalization warning, quarantine, duplicate source ID, orphan reference, and fatal mapping tests PASS.

```bash
git add backend/app/ingestion backend/tests/unit/ingestion docs/sample-data
git commit -m "feat: validate and quarantine mapped csv rows"
```

### Task 7: Build CSV connectors and atomic snapshot service

**Files:**
- Create: `backend/app/connectors/csv_source.py`
- Create: `backend/app/connectors/csv_target.py`
- Create: `backend/app/snapshots/hashing.py`
- Create: `backend/app/snapshots/service.py`
- Test: `backend/tests/contract/test_csv_connectors.py`
- Test: `backend/tests/integration/snapshots/test_snapshot_service.py`

**Interfaces:**
- Consumes: stored upload IDs, mapping version, schema version, and identical `SnapshotScope` values.
- Produces: `SnapshotPair(authoritative_id, target_id, scope, summaries)` containing published canonical entities.

- [ ] **Step 1: Write the substitution and idempotency tests**

```python
async def test_csv_source_satisfies_connector_contract(csv_source) -> None:
    assert (await csv_source.version()).value.startswith("sha256:")
    rows = [row async for row in csv_source.read(ConnectorReadRequest())]
    assert {row["entity_type"] for row in rows} == {"organization_unit", "teacher"}

async def test_same_task_files_and_mapping_return_same_snapshot(service, request) -> None:
    first = await service.ingest_pair(request, idempotency_key="pair-1")
    second = await service.ingest_pair(request, idempotency_key="pair-1")
    assert second == first
```

- [ ] **Step 2: Run connector and snapshot tests**

Run: `cd backend && uv run pytest tests/contract/test_csv_connectors.py tests/integration/snapshots/test_snapshot_service.py -q`

Expected: FAIL because CSV connectors and service do not exist.

- [ ] **Step 3: Implement the CSV source connector**

```python
class CsvSourceConnector:
    def __init__(self, path: Path, mapping: FieldMapping) -> None:
        self.path, self.mapping = path, mapping

    async def version(self) -> ConnectorVersion:
        return ConnectorVersion(value=f"sha256:{sha256_file(self.path)}")

    async def read(self, request: ConnectorReadRequest) -> AsyncIterator[dict[str, Any]]:
        frame = pl.read_csv(self.path, infer_schema_length=0)
        result = validate_batch(frame, self.mapping)
        if result.fatal_errors:
            raise IngestionBlocked(result.fatal_errors)
        for row in result.accepted:
            if request.entity_types is None or row["entity_type"] in request.entity_types:
                yield row
```

- [ ] **Step 4: Implement pair validation and publication orchestration**

```python
class SnapshotService:
    async def ingest_pair(self, request: PairedIngestionRequest, idempotency_key: str) -> SnapshotPair:
        existing = await self.tasks.find_ingestion_by_key(idempotency_key)
        if existing:
            return existing
        if request.authoritative.scope != request.target.scope:
            raise ScopeMismatch("authoritative and target scope must be identical")
        source_result, target_result = await self._load_both(request)
        if source_result.fatal_errors or target_result.fatal_errors:
            raise IngestionBlocked(source_result.fatal_errors + target_result.fatal_errors)
        return await self.snapshots.publish_pair_from_results(request, source_result, target_result)
```

- [ ] **Step 5: Verify raw/canonical hashes and commit**

Run: `cd backend && uv run pytest tests/contract/test_csv_connectors.py tests/integration/snapshots/test_snapshot_service.py -q`

Expected: connector substitution, stable row number, matching scope, immutable hash, failure atomicity, and idempotency tests PASS.

```bash
git add backend/app/connectors backend/app/snapshots backend/tests/contract backend/tests/integration/snapshots
git commit -m "feat: publish canonical csv snapshot pairs"
```

### Task 8: Expose paired upload and task creation APIs

**Files:**
- Create: `backend/app/api/routes/uploads.py`
- Create: `backend/app/api/routes/reconciliation_tasks.py`
- Create: `backend/app/api/dependencies.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/api/test_task_creation.py`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: multipart files, JSON mapping identifiers, scope metadata, `Idempotency-Key` header, and backend-owned operator dependency.
- Produces: `POST /api/uploads`, `GET /api/field-mappings`, `POST /api/uploads/{id}/mapping-preview`, `POST /api/reconciliation-tasks`, `GET /api/reconciliation-tasks/{id}`, `GET /api/reconciliation-tasks/{id}/ingestion-summary`, quarantine download, and stable problem-detail errors.

- [ ] **Step 1: Add API acceptance and rejection tests**

```python
def test_create_task_requires_both_roles(client, source_upload_id) -> None:
    response = client.post("/api/reconciliation-tasks", json={
        "authoritative_upload_id": source_upload_id,
        "tenant_id": "school-1", "scope_id": "all", "snapshot_mode": "full"
    }, headers={"Idempotency-Key": "task-1"})
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "target_upload_id"

def test_duplicate_idempotency_key_returns_same_task(client, valid_payload) -> None:
    first = client.post("/api/reconciliation-tasks", json=valid_payload, headers={"Idempotency-Key": "task-2"})
    second = client.post("/api/reconciliation-tasks", json=valid_payload, headers={"Idempotency-Key": "task-2"})
    assert second.json()["id"] == first.json()["id"]
```

- [ ] **Step 2: Run the API tests**

Run: `cd backend && uv run pytest tests/integration/api/test_task_creation.py -q`

Expected: FAIL with 404 because routes are not registered.

- [ ] **Step 3: Implement validated request and route orchestration**

```python
class CreateReconciliationTask(BaseModel):
    authoritative_upload_id: UUID
    target_upload_id: UUID
    tenant_id: str
    scope_id: str
    snapshot_mode: SnapshotMode
    entity_types: set[EntityType] = Field(default_factory=lambda: set(EntityType))
    schema_version: str = "canonical-v1"
    authoritative_mapping_version: str
    target_mapping_version: str

@router.post("", status_code=202, response_model=ReconciliationTaskResponse)
async def create_task(
    body: CreateReconciliationTask,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    service: Annotated[ReconciliationService, Depends(get_reconciliation_service)],
) -> ReconciliationTaskResponse:
    return await service.create_and_ingest(body, idempotency_key=idempotency_key)
```

```python
@router.get("/field-mappings", response_model=list[FieldMappingSummary])
async def list_field_mappings(registry=Depends(get_field_mapping_registry)):
    return registry.list_versions()

@router.post("/uploads/{upload_id}/mapping-preview", response_model=FieldMappingPreviewResponse)
async def preview_mapping(upload_id: UUID, body: FieldMappingPreviewRequest,
                          service=Depends(get_ingestion_preview_service)):
    return await service.preview(upload_id, body.mapping_version, sample_limit=5)

@router.get("/reconciliation-tasks/{task_id}/ingestion-summary", response_model=PairedIngestionSummary)
async def ingestion_summary(task_id: UUID, service=Depends(get_snapshot_service)):
    return await service.get_ingestion_summary(task_id)

@router.get("/reconciliation-tasks/{task_id}/quarantine/{source_role}")
async def download_quarantine(task_id: UUID, source_role: SourceRole, service=Depends(get_snapshot_service)):
    artifact = await service.get_quarantine_artifact(task_id, source_role)
    return FileResponse(artifact.path, media_type="text/csv", filename=f"quarantine-{source_role.value}.csv")
```

- [ ] **Step 4: Document real commands and run the ingestion suite**

Add to `AGENTS.md`: `docker compose -f infra/docker-compose.yml up -d`, `cd backend && uv sync`, `uv run alembic upgrade head`, `uv run uvicorn app.main:app --reload`, and `uv run pytest`.

Run: `cd backend && uv run pytest tests/unit/ingestion tests/contract tests/integration/api/test_task_creation.py -q && uv run ruff check . && uv run mypy app`

Expected: all ingestion tests PASS with no lint or type errors.

- [ ] **Step 5: Commit the completed ingestion module**

```bash
git add backend/app/api backend/app/main.py backend/tests/integration/api AGENTS.md
git commit -m "feat: expose paired csv ingestion api"
```

## Module Acceptance

Run: `openspec validate demo && docker compose -f infra/docker-compose.yml up -d && cd backend && uv run alembic upgrade head && uv run pytest tests/unit/ingestion tests/contract tests/integration -q`

Expected: a valid pair creates exactly two published snapshots; invalid mappings create no published snapshot; retrying the same idempotency key creates no duplicate task, upload, or snapshot; API connector stubs fail explicitly.

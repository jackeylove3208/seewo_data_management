# Chat-driven API Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect DingTalk and WeCom as read-only authoritative organization sources, reconcile
their frozen data against a MySQL target through the existing Agent Graph v2 pipeline, and configure
connections safely from conversation.

**Architecture:** A tenant-scoped connection control plane resolves audited provider Adapters and
materializes immutable API JSONL evidence. `source-ingestion-v3` routes authoritative and target
roles independently, projects API evidence directly to `AgentInputRecord`, and then reuses the
existing identity-work/AI/governance/SQL chain. Provider technical IDs stay in stable locators or
explicit external bindings, never ordinary identity postings.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 async, Alembic, httpx, cryptography
Fernet, pytest/pytest-asyncio, React 19, TypeScript, Vitest, Playwright.

## Global Constraints

- Work only in `/Users/lbs/PycharmProjects/PythonProject/.worktrees/add-chat-api-connectors`.
- New branches use the `codex/` prefix; this plan runs on `codex/add-chat-api-connectors`.
- Use `agent-sync-graph-v2`, `source-ingestion-v3`, and `deterministic-execution-v2` for new API
  tasks; do not add a Graph v3.
- Existing runs resume with their persisted Graph, ingestion, and execution versions.
- API authority is read-only; no third-party mutation capability is exposed.
- App secrets and access tokens never enter chat, task intent, model/Skill/MCP payloads, Graph
  evidence, checkpoints, events, logs, or ordinary API responses.
- Normalize API records through `AgentContractRecord` into `AgentInputRecord`; never create
  `RawSnapshotRow`, `CanonicalEntityRecord`, or legacy `EntityMapping`.
- Ordinary identity postings remain exactly `number`, `phone`, and `email`.
- Every behavior change follows red-green-refactor and includes synthetic data only.
- Backend quality gates use `.venv/bin/pytest`, `.venv/bin/ruff check .`, and
  `.venv/bin/mypy app`; frontend gates use `npm test -- --run`, `npm run lint`,
  `npm run typecheck`, and `npm run build`.

---

### Task 1: Freeze ingestion v3 and route connector roles independently

**Files:**

- Modify: `backend/app/core/config.py`
- Create: `backend/app/agent_runtime/source_bindings.py`
- Modify: `backend/app/agent_runtime/service.py`
- Modify: `backend/app/agent_graph/runtime.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Test: `backend/tests/unit/agent_runtime/test_source_bindings.py`
- Test: `backend/tests/integration/agent_runtime/test_supervisor.py`
- Test: `backend/tests/unit/agent_graph/test_runtime.py`

**Interfaces:**

- Produces:
  `AgentSourceBinding(role, connector_kind, configuration_id, snapshot_id,
  mapping_checkpoint_key, normalization_checkpoint_key)`.
- Produces:
  `resolve_source_bindings(task: ReconciliationTask) -> tuple[AgentSourceBinding, ...]`.
- Later tasks consume the bindings for materialization, inspection, mapping, and normalization.

- [ ] **Step 1: Write failing role-binding tests**

```python
def test_api_database_roles_are_resolved_independently() -> None:
    task = SimpleNamespace(
        agent_intent={
            "source": {"kind": "api", "configuration_id": "ding-school"},
            "target": {"kind": "database", "configuration_id": "seewo-mysql"},
        }
    )

    authority, target = resolve_source_bindings(task)

    assert authority.role == "authoritative"
    assert authority.connector_kind == "api"
    assert authority.mapping_checkpoint_key == "graph-api-field-mapping-v3:authoritative"
    assert target.role == "target"
    assert target.connector_kind == "database"
    assert target.mapping_checkpoint_key == "graph-database-field-mapping-v3:target"
```

```python
def test_legacy_run_keeps_its_frozen_ingestion_contract() -> None:
    run = AgentRunRecord(ingestion_contract_version="source-ingestion-v2")
    settings = Settings(source_ingestion_v3_enabled=True)
    assert select_ingestion_contract(run=run, task=None, settings=settings) == "source-ingestion-v2"
```

- [ ] **Step 2: Run tests and verify expected RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/agent_runtime/test_source_bindings.py \
  tests/integration/agent_runtime/test_supervisor.py \
  tests/unit/agent_graph/test_runtime.py -q
```

Expected: collection/import failure for `app.agent_runtime.source_bindings` or assertion failure
because API tasks still select the old runtime path.

- [ ] **Step 3: Add the immutable role-binding contract**

```python
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.models.reconciliation import ReconciliationTask

SourceRoleName = Literal["authoritative", "target"]
ConnectorKind = Literal["csv", "local", "remote_csv", "database", "api"]


@dataclass(frozen=True, slots=True)
class AgentSourceBinding:
    role: SourceRoleName
    connector_kind: ConnectorKind
    configuration_id: str | None
    snapshot_id: UUID | None
    mapping_checkpoint_key: str
    normalization_checkpoint_key: str


def resolve_source_bindings(task: ReconciliationTask) -> tuple[AgentSourceBinding, ...]:
    if not isinstance(task.agent_intent, dict):
        raise ValueError("Agent task intent is missing")
    resolved: list[AgentSourceBinding] = []
    for role, intent_key in (("authoritative", "source"), ("target", "target")):
        selection = task.agent_intent.get(intent_key)
        if not isinstance(selection, dict):
            raise ValueError(f"Agent {role} selection is missing")
        kind = selection.get("kind")
        if kind not in {"csv", "local", "remote_csv", "database", "api"}:
            raise ValueError(f"Agent {role} connector kind is invalid")
        configuration_id = selection.get("configuration_id")
        mapping_kind = "api" if kind == "api" else "database" if kind == "database" else "csv"
        resolved.append(
            AgentSourceBinding(
                role=role,
                connector_kind=kind,
                configuration_id=configuration_id if isinstance(configuration_id, str) else None,
                snapshot_id=None,
                mapping_checkpoint_key=f"graph-{mapping_kind}-field-mapping-v3:{role}",
                normalization_checkpoint_key=f"graph-source-normalization-v3:{role}",
            )
        )
    return tuple(resolved)
```

- [ ] **Step 4: Freeze v3 only for newly created API tasks**

Add `source_ingestion_v3_enabled: bool = False` to `Settings`. In
`AgentSupervisorService.start()`, compute versions once:

```python
api_authority = _uses_api_authority(task)
ingestion_contract_version = (
    "source-ingestion-v3"
    if api_authority
    and self.settings is not None
    and self.settings.source_ingestion_v3_enabled
    else "source-ingestion-v2"
    if self.settings is not None
    and self.settings.source_ingestion_v2_enabled
    and task.workflow_version == "agent-graph-v1"
    else "model-mediated-ingestion-v1"
)
graph_version = (
    "agent-sync-graph-v2"
    if _uses_remote_csv(task) or api_authority
    else "agent-sync-graph-v1"
)
```

Do not recompute either version when an existing `AgentRunRecord` is returned.

- [ ] **Step 5: Route v3 inspection and normalization by role**

In Graph candidate generation and execution, branch only on persisted
`context.ingestion_contract_version == "source-ingestion-v3"` and resolve the action's role binding.
Keep existing v1/v2 branches byte-for-byte compatible.

```python
if context.ingestion_contract_version == "source-ingestion-v3":
    binding = await self._source_binding(context.task_id, role)
    if binding.connector_kind == "api":
        return await self._inspect_api_source_v3(context, action, binding)
    if binding.connector_kind == "database":
        return await self._inspect_database_source_v3(context, action, binding)
    raise GraphGuardRejected("ingestion_v3_connector_kind_unsupported")
```

- [ ] **Step 6: Run focused and existing version-regression tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/agent_runtime/test_source_bindings.py \
  tests/integration/agent_runtime/test_supervisor.py \
  tests/unit/agent_graph/test_runtime.py \
  tests/integration/agent_graph/test_production_runtime.py -q
```

Expected: PASS with old v1/v2 assertions unchanged and new API tasks frozen to v2/v3.

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/config.py backend/app/agent_runtime/source_bindings.py \
  backend/app/agent_runtime/service.py backend/app/agent_graph/runtime.py \
  backend/app/agent_graph/production_executor.py \
  backend/tests/unit/agent_runtime/test_source_bindings.py \
  backend/tests/integration/agent_runtime/test_supervisor.py \
  backend/tests/unit/agent_graph/test_runtime.py
git commit -m "feat: add role-bound source ingestion v3"
```

### Task 2: Add connection, API source, secret, and external-binding persistence

**Files:**

- Create: `backend/app/models/api_connectors.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0039_api_connectors.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements-ci.txt`
- Test: `backend/tests/unit/models/test_api_connector_models.py`
- Test: `backend/tests/integration/test_migrations.py`

**Interfaces:**

- Produces SQLAlchemy models:
  `ApiConnectionRecord`, `ApiConnectionSecretRecord`, `ApiAuthoritySourceRecord`,
  `AgentExternalIdentityBindingRecord`.
- `ApiConnectionRecord.secret_ref` points to `db-secret:<secret-record-id>`.
- Later repositories depend on uniqueness and tenant indexes defined here.

- [ ] **Step 1: Write failing model and metadata tests**

```python
def test_api_connection_contains_only_a_secret_reference() -> None:
    record = ApiConnectionRecord(
        tenant_id="school-1",
        provider_id="dingtalk",
        display_name="钉钉通讯录",
        public_configuration={"organization_ref": "school-1"},
        secret_ref="db-secret:00000000-0000-0000-0000-000000000001",
        manifest_version="1.0.0",
        adapter_version="1.0.0",
        capabilities={"department": True, "teacher": True},
        visibility_summary={"visible": True, "record_count": 10},
        state="active",
        created_by="operator-1",
        updated_by="operator-1",
    )
    assert "secret" not in record.public_configuration
    assert record.secret_ref.startswith("db-secret:")


def test_external_binding_is_unique_per_authority_locator() -> None:
    constraints = {
        constraint.name for constraint in AgentExternalIdentityBindingRecord.__table__.constraints
    }
    assert "uq_agent_external_binding_authority" in constraints
```

- [ ] **Step 2: Run tests and verify expected RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/models/test_api_connector_models.py -q
```

Expected: import failure for `app.models.api_connectors`.

- [ ] **Step 3: Add additive models**

Define UUID primary keys, tenant indexes, timestamps, foreign keys, and check constraints. Required
columns:

```python
class ApiConnectionRecord(Base, TimestampMixin):
    __tablename__ = "api_connections"
    id: Mapped[UUID]
    tenant_id: Mapped[str]
    provider_id: Mapped[str]
    display_name: Mapped[str]
    public_configuration: Mapped[dict[str, Any]]
    secret_ref: Mapped[str]
    manifest_version: Mapped[str]
    adapter_version: Mapped[str]
    capabilities: Mapped[dict[str, Any]]
    visibility_summary: Mapped[dict[str, Any]]
    state: Mapped[str]
    last_tested_at: Mapped[datetime | None]
    last_safe_error_code: Mapped[str | None]
    created_by: Mapped[str]
    updated_by: Mapped[str]


class ApiConnectionSecretRecord(Base, TimestampMixin):
    __tablename__ = "api_connection_secrets"
    id: Mapped[UUID]
    tenant_id: Mapped[str]
    ciphertext: Mapped[bytes]
    key_version: Mapped[str]


class ApiAuthoritySourceRecord(Base, TimestampMixin):
    __tablename__ = "api_authority_sources"
    id: Mapped[UUID]
    tenant_id: Mapped[str]
    task_id: Mapped[UUID]
    connection_id: Mapped[UUID]
    selected_entities: Mapped[list[str]]
    selection_hash: Mapped[str]
    state: Mapped[str]
    source_file_id: Mapped[UUID | None]
    snapshot_id: Mapped[UUID | None]
    content_sha256: Mapped[str | None]
    record_count: Mapped[int | None]
    page_count: Mapped[int | None]
    safe_problem_code: Mapped[str | None]


class AgentExternalIdentityBindingRecord(Base, TimestampMixin):
    __tablename__ = "agent_external_identity_bindings"
    id: Mapped[UUID]
    tenant_id: Mapped[str]
    provider_id: Mapped[str]
    connection_id: Mapped[UUID]
    entity_kind: Mapped[str]
    authority_stable_locator: Mapped[str]
    target_connector_id: Mapped[str]
    target_stable_locator: Mapped[str]
    status: Mapped[str]
    binding_version: Mapped[int]
    confirmed_by: Mapped[str]
    confirmed_at: Mapped[datetime]
    revoked_by: Mapped[str | None]
    revoked_at: Mapped[datetime | None]
    evidence_hash: Mapped[str]
```

- [ ] **Step 4: Add migration 0039**

Create the four tables with matching check/unique/index definitions, using
`down_revision = "0038_expand_storage_name"`. Downgrade drops only these four additive tables in
reverse dependency order.

- [ ] **Step 5: Add pinned encryption dependency**

Add `cryptography` to backend runtime dependencies and pin the resolved compatible version in
`requirements-ci.txt`. Use Fernet only inside the secret-store module in Task 3.

- [ ] **Step 6: Run model and migration tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/models/test_api_connector_models.py \
  tests/integration/test_migrations.py -q
.venv/bin/alembic upgrade head --sql >/tmp/api-connectors-migration.sql
```

Expected: model tests pass; ordinary migration tests pass/skip according to configured PostgreSQL
URL; Alembic generates SQL through revision 0039.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/api_connectors.py backend/app/models/__init__.py \
  backend/alembic/versions/0039_api_connectors.py backend/pyproject.toml \
  backend/requirements-ci.txt backend/tests/unit/models/test_api_connector_models.py \
  backend/tests/integration/test_migrations.py
git commit -m "feat: persist secure API connector metadata"
```

### Task 3: Implement the provider registry and encrypted secret boundary

**Files:**

- Create: `backend/app/api_connectors/__init__.py`
- Create: `backend/app/api_connectors/contracts.py`
- Create: `backend/app/api_connectors/registry.py`
- Create: `backend/app/api_connectors/secrets.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/api_connectors/test_registry.py`
- Test: `backend/tests/unit/api_connectors/test_secrets.py`

**Interfaces:**

- Produces immutable `ProviderManifest`, `SafeApiConnection`, `ConnectionTestResult`,
  `FrozenApiRecord`, and `CaptureResult`.
- Produces `OrganizationApiAdapter` protocol.
- Produces `ProviderRegistry.manifest(provider_id)` and `.adapter(provider_id)`.
- Produces `EncryptedDatabaseSecretStore.put/get/rotate`.

- [ ] **Step 1: Write failing registry and secret tests**

```python
def test_registry_rejects_duplicate_provider() -> None:
    registry = ProviderRegistry()
    registry.register(DINGTALK_MANIFEST, FakeAdapter())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(DINGTALK_MANIFEST, FakeAdapter())


async def test_secret_store_round_trips_without_plaintext_in_database(session) -> None:
    store = EncryptedDatabaseSecretStore(session, fernet_key=FERNET_KEY)
    secret_ref = await store.put(
        tenant_id="school-1",
        payload={"app_key": "app", "app_secret": "secret"},
    )
    row = await session.scalar(select(ApiConnectionSecretRecord))
    assert row is not None
    assert b"secret" not in row.ciphertext
    assert await store.get(tenant_id="school-1", secret_ref=secret_ref) == {
        "app_key": "app",
        "app_secret": "secret",
    }
```

- [ ] **Step 2: Run tests and verify expected RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/api_connectors/test_registry.py \
  tests/unit/api_connectors/test_secrets.py -q
```

Expected: import failures for the new provider modules.

- [ ] **Step 3: Add provider contracts**

```python
class ProviderManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider_id: str
    manifest_version: str
    adapter_version: str
    supported_entities: frozenset[AgentEntityKind]
    required_secret_fields: tuple[str, ...]
    endpoint_hosts: tuple[str, ...]
    projection_version: str


class OrganizationApiAdapter(Protocol):
    manifest: ProviderManifest

    async def test_connection(
        self, public_configuration: Mapping[str, object], secret: Mapping[str, str]
    ) -> ConnectionTestResult: ...

    async def capture(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        selected_entities: frozenset[AgentEntityKind],
    ) -> AsyncIterator[CapturedApiPage]: ...

    def project(
        self, record: FrozenApiRecord, context: AgentProjectionContext
    ) -> AgentContractRecord: ...
```

- [ ] **Step 4: Implement registry and encrypted store**

Registry registration validates matching manifest/Adapter IDs. Secret `put()` serializes sorted
JSON, encrypts with Fernet, and stores only ciphertext. `get()` verifies `db-secret:` syntax,
tenant ownership, decryption, JSON object shape, and string values. `rotate()` creates new
ciphertext and updates the connection reference in one transaction.

Add `api_connector_secret_key: SecretStr | None = None`; when API connectors are enabled, settings
validation requires a valid Fernet key.

- [ ] **Step 5: Run focused tests and security configuration tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/api_connectors/test_registry.py \
  tests/unit/api_connectors/test_secrets.py \
  tests/unit/core/test_config.py tests/unit/security/test_env_example.py -q
```

Expected: PASS and no secret value appears in assertion output or serialized safe views.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api_connectors backend/app/core/config.py \
  backend/tests/unit/api_connectors backend/tests/unit/core/test_config.py \
  backend/tests/unit/security/test_env_example.py
git commit -m "feat: add audited API provider registry"
```

### Task 4: Add tenant-scoped connection services, routes, and provider adapters

**Files:**

- Create: `backend/app/api_connectors/repository.py`
- Create: `backend/app/api_connectors/service.py`
- Create: `backend/app/api_connectors/dingtalk.py`
- Create: `backend/app/api_connectors/wecom.py`
- Create: `backend/app/schemas/api_connectors.py`
- Create: `backend/app/api/routes/api_connectors.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/contract/test_organization_api_adapters.py`
- Test: `backend/tests/integration/api/test_api_connectors.py`

**Interfaces:**

- Produces `ApiConnectionService.create/list/get/test/rotate/delete`.
- Produces REST endpoints under `/api/connectors`.
- Registers `DingtalkOrganizationAdapter` and `WeComOrganizationAdapter`.

- [ ] **Step 1: Write failing API and shared Adapter contract tests**

```python
@pytest.mark.parametrize("adapter_factory", [dingtalk_adapter, wecom_adapter])
async def test_adapter_contract_closes_pagination_and_keeps_external_id_out_of_number(
    adapter_factory,
) -> None:
    adapter, server = adapter_factory()
    pages = [
        page
        async for page in adapter.capture(
            public_configuration={"organization_ref": "school-1"},
            secret=server.valid_secret,
            selected_entities=frozenset({AgentEntityKind.TEACHER}),
        )
    ]
    assert pages[-1].next_cursor is None
    record = adapter.project(pages[0].records[0], projection_context())
    assert record.stable_locator.startswith("api:")
    assert record.number != pages[0].records[0].external_id


async def test_connection_response_never_returns_secret(client) -> None:
    response = await client.post(
        "/api/connectors/connections",
        json={
            "provider_id": "dingtalk",
            "display_name": "学校钉钉",
            "public_configuration": {"organization_ref": "school-1"},
            "secret": {"app_key": "app", "app_secret": "secret"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert "secret" not in json.dumps(body).lower()
```

- [ ] **Step 2: Run tests and verify expected RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/contract/test_organization_api_adapters.py \
  tests/integration/api/test_api_connectors.py -q
```

Expected: missing route and Adapter imports.

- [ ] **Step 3: Implement safe connection schemas and service**

Request schemas accept secret fields only on create/rotate. Response schemas contain:

```python
class ApiConnectionRead(BaseModel):
    id: UUID
    provider_id: str
    display_name: str
    public_configuration: dict[str, object]
    manifest_version: str
    adapter_version: str
    capabilities: dict[str, object]
    visibility_summary: dict[str, object]
    state: Literal["pending", "active", "invalid", "disabled"]
    last_tested_at: datetime | None
    last_safe_error_code: str | None
```

Every repository query includes `tenant_id`. `test()` resolves the secret backend-side, invokes the
registered Adapter, persists only safe capability/visibility/error facts, and never creates a task.

- [ ] **Step 4: Implement DingTalk and WeCom Adapters**

Use one injected `httpx.AsyncClient` per Adapter. Enforce manifest hosts and fixed paths. DingTalk
uses the v1 access-token endpoint and token header; WeCom uses the fixed gettoken, department, and
user endpoints. Both translate responses to stable safe codes, enforce bounded pages, reject
repeated cursors/external IDs, and never log request authorization.

Provider-specific raw records remain in capture artifacts; six-field projection uses only manifest
or connection-audited field mappings.

- [ ] **Step 5: Implement routes and app registration**

Add:

```text
GET    /api/connectors/providers
POST   /api/connectors/configuration-sessions
POST   /api/connectors/connections
GET    /api/connectors/connections
GET    /api/connectors/connections/{id}
POST   /api/connectors/connections/{id}/test
POST   /api/connectors/connections/{id}/rotate-secret
DELETE /api/connectors/connections/{id}
```

Obtain operator/tenant identity through existing authenticated backend context, never request it in
the payload.

- [ ] **Step 6: Run contract, API, and security tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/contract/test_organization_api_adapters.py \
  tests/integration/api/test_api_connectors.py \
  tests/integration/ai/test_agent_phase_gateway.py \
  tests/unit/ai/test_agent_tool_authorization.py -q
```

Expected: PASS, including 401/403, empty visibility, rate limit, timeout, duplicate cursor, duplicate
ID, and cross-tenant cases.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api_connectors backend/app/schemas/api_connectors.py \
  backend/app/api/routes/api_connectors.py backend/app/main.py \
  backend/tests/contract/test_organization_api_adapters.py \
  backend/tests/integration/api/test_api_connectors.py
git commit -m "feat: manage DingTalk and WeCom connections"
```

### Task 5: Materialize API authority through Graph v2

**Files:**

- Create: `backend/app/api_connectors/materializer.py`
- Modify: `backend/app/agent_runtime/task_service.py`
- Modify: `backend/app/agent_runtime/service.py`
- Modify: `backend/app/agent_graph/runtime.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Test: `backend/tests/integration/api_connectors/test_materializer.py`
- Test: `backend/tests/integration/agent_graph/test_production_runtime.py`

**Interfaces:**

- Produces `ApiAuthorityMaterializer.materialize(session, task_id, api_source_id) -> SourceFile`.
- Task creation binds one `ApiAuthoritySourceRecord`.
- Graph v2 dispatches `api-source:<id>` under existing action kind
  `materialize_remote_authority`.

- [ ] **Step 1: Write failing task and Graph materialization tests**

```python
async def test_api_task_selects_graph_v2_and_materializes_before_inspection(
    api_task_factory,
) -> None:
    task, run = await api_task_factory()
    graph = await graph_repository.get_run_for_agent_run(run.id)
    assert graph.graph_version == "agent-sync-graph-v2"
    assert graph.current_node == "materialize_sources"
    actions = await candidate_provider(graph_context(graph))
    assert actions[0].graph_action_kind == "materialize_remote_authority"
    assert actions[0].resource_ids == (f"api-source:{task.api_source_id}",)


async def test_partial_api_capture_is_not_published(materializer, failing_adapter, session) -> None:
    with pytest.raises(ApiSourceFailure, match="connector_pagination_incomplete"):
        await materializer.materialize(session, task_id=TASK_ID, api_source_id=SOURCE_ID)
    source = await session.get(ApiAuthoritySourceRecord, SOURCE_ID)
    assert source.state == "failed"
    assert source.source_file_id is None
    assert await session.scalar(select(Snapshot)) is None
```

- [ ] **Step 2: Run tests and verify expected RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api_connectors/test_materializer.py \
  tests/integration/agent_graph/test_production_runtime.py -q
```

Expected: API task capability rejection or missing materializer.

- [ ] **Step 3: Accept and bind `api + database` tasks**

Add a specific `_validate_api_database_pair()` before the mixed-connector rejection. It verifies
feature flags, tenant-owned active connection, authoritative role, selected entities, tested
capabilities/visibility, target database role/capabilities, and MySQL dialect.

`_bind_api_database_pair()` creates the target database SourceFile/Snapshot exactly as the existing
database binding does and creates one task-bound API source record; it does not call the provider.

- [ ] **Step 4: Implement atomic JSONL materialization**

The materializer:

1. locks the task-bound API source row;
2. resolves safe connection + secret + Adapter;
3. writes sorted JSONL records to a unique `.part`;
4. validates final cursor, selected entities, duplicate IDs, count, and maximum pages;
5. hashes and atomically renames the artifact;
6. creates authoritative `SourceFile` and `Snapshot`;
7. marks the API source ready in the same transaction;
8. returns the existing ready source for identical replay.

Use managed storage under `settings.upload_root / "api-authority"` and never include provider secrets
or token material in the JSONL header.

- [ ] **Step 5: Dispatch API resources without a Graph definition change**

At `materialize_sources`, candidate generation first resolves a task-bound API source; otherwise it
uses the existing remote source path. Executor dispatches by resource prefix while retaining
`action.graph_action_kind == "materialize_remote_authority"`.

- [ ] **Step 6: Run materialization and Graph regressions**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api_connectors/test_materializer.py \
  tests/integration/remote_sources/test_materializer.py \
  tests/integration/agent_graph/test_production_runtime.py \
  tests/unit/agent_graph/test_definition.py -q
```

Expected: API and remote CSV materialization pass; Graph definition node count/version assertions
remain unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api_connectors/materializer.py \
  backend/app/agent_runtime/task_service.py backend/app/agent_runtime/service.py \
  backend/app/agent_graph/runtime.py backend/app/agent_graph/production_executor.py \
  backend/tests/integration/api_connectors/test_materializer.py \
  backend/tests/integration/agent_graph/test_production_runtime.py
git commit -m "feat: materialize API authority in graph v2"
```

### Task 6: Normalize API evidence and preserve unavailable-field semantics

**Files:**

- Create: `backend/app/ingestion/agent_api_adapter.py`
- Modify: `backend/app/ingestion/agent_contract.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Modify: `backend/app/agent_graph/runtime.py`
- Modify: `backend/app/reconciliation/agent_identity.py`
- Test: `backend/tests/unit/ingestion/test_agent_api_adapter.py`
- Test: `backend/tests/unit/ingestion/test_agent_contract.py`
- Test: `backend/tests/unit/reconciliation/test_agent_identity.py`
- Test: `backend/tests/integration/agent_graph/test_production_runtime.py`

**Interfaces:**

- Produces:
  `AgentApiIngestionAdapter.extract(artifact_path, adapter, context) -> AgentIngestionOutcome`.
- Adds included mark `authority_field_unavailable` with affected fields.
- Produces governed-field masks consumed by ordinary comparison.

- [ ] **Step 1: Write failing Adapter and unavailable-field tests**

```python
def test_api_adapter_emits_stable_agent_contract_without_userid_posting(tmp_path) -> None:
    outcome = AgentApiIngestionAdapter().extract(
        artifact_path=write_api_artifact(tmp_path, external_id="user-42", phone=None),
        adapter=FakeProviderAdapter(unavailable_fields={"phone"}),
        context=authority_projection_context(),
    )
    record = outcome.records[0]
    assert record.stable_locator == "api:connection-1:teacher:user-42"
    assert record.number is None
    assert ("phone",) == outcome.marks[0].affected_fields
    assert identity_postings(record) == ()


def test_unavailable_authority_field_is_not_a_difference() -> None:
    differences = ordinary_field_differences(
        authority_record(phone=None),
        target_record(phone="13800000000"),
        unavailable_fields=frozenset({"phone"}),
    )
    assert "phone" not in differences
```

- [ ] **Step 2: Run tests and verify expected RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/ingestion/test_agent_api_adapter.py \
  tests/unit/reconciliation/test_agent_identity.py -q
```

Expected: missing Adapter and unsupported `unavailable_fields` argument.

- [ ] **Step 3: Implement deterministic artifact extraction**

Parse the materializer's versioned JSONL header and records. Validate task/snapshot/connection,
Adapter/projection version, selected entity set, sorted unique locators, and maximum locator length.
For each record call `adapter.project()` and set deterministic stable order.

Add one included input mark per affected record:

```python
AgentInputMark(
    input_record_id=UUID(int=0),
    reason_code="authority_field_unavailable",
    affected_fields=tuple(sorted(unavailable_fields)),
    inclusion_state="included",
    report_disposition="source_field_unavailable",
    safe_evidence={
        "code": "authority_field_unavailable",
        "field_count": len(unavailable_fields),
        "source_role": "authoritative",
    },
)
```

- [ ] **Step 4: Persist v3 API and database roles**

For API authority normalization, load its ready Snapshot and frozen Adapter contract, extract,
persist inputs/marks, and save `graph-source-normalization-v3:authoritative` with `model_calls=0`.

For database target, reuse `AgentDatabaseIngestionAdapter` but read the role-specific v3 database
mapping and checkpoint key. Create the existing target version.

- [ ] **Step 5: Exclude unavailable fields from ordinary differences**

Load `authority_field_unavailable` marks by authoritative input ID in the identity builder and pass
the affected field set into:

```python
def ordinary_field_differences(
    authority: AgentIdentityRecord,
    target: AgentIdentityRecord,
    *,
    unavailable_fields: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    return tuple(
        field
        for field in governed_fields(authority.entity_kind)
        if field not in unavailable_fields
        and _semantic_field_value(authority, field) != _semantic_field_value(target, field)
    )
```

- [ ] **Step 6: Run ingestion and identity tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/ingestion/test_agent_api_adapter.py \
  tests/unit/ingestion/test_agent_contract.py \
  tests/unit/reconciliation/test_agent_identity.py \
  tests/integration/repositories/test_agent_analysis_repository.py \
  tests/integration/agent_graph/test_production_runtime.py -q
```

Expected: PASS; API known-source inspection/normalization invocation facts report zero model calls.

- [ ] **Step 7: Commit**

```bash
git add backend/app/ingestion/agent_api_adapter.py \
  backend/app/ingestion/agent_contract.py backend/app/agent_graph/production_executor.py \
  backend/app/agent_graph/runtime.py backend/app/reconciliation/agent_identity.py \
  backend/tests/unit/ingestion/test_agent_api_adapter.py \
  backend/tests/unit/ingestion/test_agent_contract.py \
  backend/tests/unit/reconciliation/test_agent_identity.py \
  backend/tests/integration/agent_graph/test_production_runtime.py
git commit -m "feat: normalize API evidence to Agent inputs"
```

### Task 7: Apply audited external identity bindings

**Files:**

- Create: `backend/app/repositories/agent_external_identity.py`
- Create: `backend/app/agent_runtime/external_identity_service.py`
- Modify: `backend/app/reconciliation/agent_identity.py`
- Modify: `backend/app/schemas/api_connectors.py`
- Modify: `backend/app/api/routes/api_connectors.py`
- Test: `backend/tests/integration/repositories/test_agent_external_identity.py`
- Test: `backend/tests/unit/reconciliation/test_agent_identity.py`
- Test: `backend/tests/integration/api/test_api_connectors.py`

**Interfaces:**

- Produces `AgentExternalIdentityRepository.active_for_run_scope(...)`.
- Produces `AgentExternalIdentityService.confirm/revoke`.
- Identity builder creates normal `AgentIdentityClaimRecord` from valid binding evidence.

- [ ] **Step 1: Write failing binding and identity tests**

```python
async def test_valid_binding_claims_records_without_ordinary_keys(session, seeded_inputs) -> None:
    await seed_binding(
        session,
        authority_stable_locator="api:connection-1:teacher:user-42",
        target_stable_locator="database:seewo-mysql:teacher-9",
    )
    await AgentIdentityIndexBuilder(session).build(run_id=seeded_inputs.run_id)
    claim = await session.scalar(select(AgentIdentityClaimRecord))
    assert claim.authority_input_id == seeded_inputs.authority_id
    assert claim.target_input_id == seeded_inputs.target_id


async def test_no_key_and_no_binding_creates_authority_invalid(session, seeded_inputs) -> None:
    await AgentIdentityIndexBuilder(session).build(run_id=seeded_inputs.run_id)
    work = await session.scalar(select(AgentWorkItemRecord))
    assert work.kind == "authority_invalid"
    mark = await session.scalar(select(AgentInputMarkRecord))
    assert mark.reason_code == "authority_identity_absent"
```

- [ ] **Step 2: Run tests and verify expected RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/repositories/test_agent_external_identity.py \
  tests/unit/reconciliation/test_agent_identity.py -q
```

Expected: missing repository/service and current no-key behavior creates `target_missing`.

- [ ] **Step 3: Implement binding confirmation and revocation**

Confirmation verifies tenant ownership, active provider connection, exact current authority/target
stable locators, entity kind, one-to-one uniqueness, authenticated operator, and evidence hash.
Revocation changes status and records actor/time; records are never hard-deleted.

- [ ] **Step 4: Apply bindings before ordinary postings**

The identity builder:

1. loads valid inputs and unavailable marks;
2. loads active bindings scoped to current provider connection and target connector;
3. resolves each locator against current Agent inputs;
4. creates claims for valid one-to-one bindings;
5. creates conflicts for contradictions or duplicate claims;
6. builds ordinary postings for remaining records;
7. creates `authority_invalid` plus `authority_identity_absent` for remaining no-key authority.

Never add provider userid to `AgentIdentityPostingRecord`.

- [ ] **Step 5: Add management endpoints**

Add authenticated list/confirm/revoke endpoints under
`/api/agent/external-identity-bindings`. Return safe locators and audit metadata; do not return raw
provider payloads or student phone.

- [ ] **Step 6: Run binding, API, and work-item tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/repositories/test_agent_external_identity.py \
  tests/unit/reconciliation/test_agent_identity.py \
  tests/integration/api/test_api_connectors.py \
  tests/integration/agent_graph/test_analysis_path.py -q
```

Expected: PASS for valid, stale, missing-target, duplicate-target, disagreement, revocation, and
cross-tenant cases.

- [ ] **Step 7: Commit**

```bash
git add backend/app/repositories/agent_external_identity.py \
  backend/app/agent_runtime/external_identity_service.py \
  backend/app/reconciliation/agent_identity.py backend/app/schemas/api_connectors.py \
  backend/app/api/routes/api_connectors.py \
  backend/tests/integration/repositories/test_agent_external_identity.py \
  backend/tests/unit/reconciliation/test_agent_identity.py \
  backend/tests/integration/api/test_api_connectors.py
git commit -m "feat: apply audited external identity bindings"
```

### Task 8: Add safe conversational connection configuration and task start

**Files:**

- Modify: `backend/app/schemas/agent_conversation.py`
- Modify: `backend/app/ai/conversation_agent.py`
- Modify: `backend/app/api/routes/agent.py`
- Modify: `backend/app/ai/skills/converse-school-data-sync/SKILL.md`
- Modify: `frontend/src/api/agent.ts`
- Create: `frontend/src/features/task-create/ApiConnectionCard.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Test: `backend/tests/unit/ai/test_conversation_agent.py`
- Test: `backend/tests/integration/api/test_agent_api.py`
- Test: `frontend/src/api/agent.test.ts`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

**Interfaces:**

- Conversation context gains `available_api_connections: tuple[ConversationApiConnection, ...]`.
- Typed cards expose provider, safe connection state, capability/visibility, and one-time
  configuration-session reference.
- Confirmed task intent uses `source.kind="api"` and a connection ID only.

- [ ] **Step 1: Write failing backend and frontend conversation tests**

```python
def test_conversation_context_contains_only_safe_api_connection() -> None:
    connection = ConversationApiConnection(
        connection_id=CONNECTION_ID,
        provider_id="dingtalk",
        display_name="学校钉钉",
        state="active",
        supported_entities=("department", "teacher"),
        visibility_state="visible",
    )
    assert "secret" not in connection.model_dump_json().lower()
```

```tsx
it("opens secure configuration without asking for the app secret in chat", async () => {
  render(<ConversationCreatePage />);
  await user.type(screen.getByRole("textbox"), "同步钉钉教师");
  await user.click(screen.getByRole("button", { name: "发送" }));
  expect(await screen.findByRole("button", { name: "安全配置钉钉" })).toBeVisible();
  expect(screen.queryByLabelText(/AppSecret/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests and verify expected RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/ai/test_conversation_agent.py \
  tests/integration/api/test_agent_api.py -q
cd ../frontend
npm test -- --run src/api/agent.test.ts \
  src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: missing safe API connection context/card and API task start remains rejected.

- [ ] **Step 3: Extend backend conversation contracts and Skill**

Add immutable safe connection views to the conversation context. The Skill can select only a listed
connection ID/provider/entity set, request a secure configuration session when missing, or surface a
safe connection problem. It has no secret-resolution or HTTP tool.

Conversation task confirmation calls the normal `AgentTaskService.create()` with:

```python
AgentTaskIntent(
    title=decision.title,
    source=AgentConnectorSelection(
        kind="api",
        configuration_id=decision.source_configuration_id,
    ),
    target=AgentConnectorSelection(
        kind="database",
        configuration_id=decision.target_configuration_id,
    ),
    entity_types=decision.entity_types,
)
```

- [ ] **Step 4: Add frontend safe connection card**

The card renders provider/display name, active/invalid state, supported entity labels, visibility
summary, test/retry action, secure configure/rotate action, and safe problem text. The secure form
posts directly to connector endpoints and clears secret input state after submission.

Never copy secret input into conversation messages, URL state, local storage, analytics, or error
text.

- [ ] **Step 5: Run conversation tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/ai/test_conversation_agent.py \
  tests/integration/api/test_agent_api.py -q
cd ../frontend
npm test -- --run src/api/agent.test.ts \
  src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: PASS for configure, test, permission error, empty visibility, retry, explicit
confirmation, idempotent start, and no-secret rendering.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/agent_conversation.py backend/app/ai/conversation_agent.py \
  backend/app/api/routes/agent.py \
  backend/app/ai/skills/converse-school-data-sync/SKILL.md \
  backend/tests/unit/ai/test_conversation_agent.py \
  backend/tests/integration/api/test_agent_api.py frontend/src/api/agent.ts \
  frontend/src/api/agent.test.ts \
  frontend/src/features/task-create/ApiConnectionCard.tsx \
  frontend/src/features/task-create/ConversationCreatePage.tsx \
  frontend/src/features/task-create/ConversationCreatePage.test.tsx
git commit -m "feat: configure API connectors from conversation"
```

### Task 9: Verify the full Agent pipeline and delivery gates

**Files:**

- Modify: `backend/tests/e2e/test_agent_graph_lifecycle.py`
- Modify: `backend/tests/e2e/test_governance_execution.py`
- Modify: `frontend/tests/e2e/agent-workflow.spec.ts`
- Modify: `backend/README.md`
- Modify: `backend/.env.example`
- Modify: `AGENTS.md`
- Modify: `openspec/changes/add-chat-api-connectors/tasks.md`

**Interfaces:**

- No new production interface.
- Proves the persisted flow:
  API Snapshot → AgentInputRecord → posting/binding → claim → work item → AI batch → finding →
  approval/plan → SQL operation → verification → report.

- [ ] **Step 1: Add a failing synthetic end-to-end API-to-MySQL test**

The test uses a synthetic provider server and configured fake MySQL connector. Assert:

```python
assert graph.graph_version == "agent-sync-graph-v2"
assert run.ingestion_contract_version == "source-ingestion-v3"
assert await count(AgentInputRecord, source_role="authoritative") > 0
assert await count(AgentIdentityClaimRecord) > 0
assert await count(AgentWorkItemRecord) > 0
assert await count(AgentModelBatchRecord) > 0
assert await count(AgentFindingRecord) > 0
assert await count(AgentGovernancePlanRecord) == 1
assert await count(AgentGovernanceOperationRecord, status="succeeded") > 0
assert not await any_rows(RawSnapshotRow)
assert not await any_rows(CanonicalEntityRecord)
assert not await any_rows(EntityMapping)
```

- [ ] **Step 2: Run end-to-end tests and verify expected RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/e2e/test_agent_graph_lifecycle.py \
  tests/e2e/test_governance_execution.py -q
```

Expected: the new API scenario fails at its first uncompleted boundary; existing scenarios pass.

- [ ] **Step 3: Complete only missing integration wiring**

Wire provider registry, secret store, connection service, materializer, database connector registry,
Graph candidate/executor, and conversation service through FastAPI app state and worker
construction. Do not add model-facing credential or arbitrary-network tools.

- [ ] **Step 4: Verify target drift and safe execution**

Add scenarios for:

- target version unchanged → deterministic SQL operations execute and verify;
- target version changed after plan → `cross_phase_replan` gate and no write;
- one SQL operation fails → dependent work blocks, independent work continues;
- API credentials rotate after snapshot → current run continues from frozen evidence;
- old v2 run resumes after v3 flag → old path only.

- [ ] **Step 5: Update documentation and environment contract**

Document the Fernet key, feature flag, synthetic provider endpoints, safe connection API, worker
startup, migration, tests, provider rollout, secret rotation, rollback, and the fact that production
provider data is never used in fixtures.

- [ ] **Step 6: Run focused end-to-end tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/e2e/test_agent_graph_lifecycle.py \
  tests/e2e/test_governance_execution.py \
  tests/integration/agent_graph/test_production_runtime.py -q
cd ../frontend
npm run test:e2e -- agent-workflow.spec.ts
```

Expected: PASS.

- [ ] **Step 7: Run all backend and frontend quality gates**

Run:

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
cd ../frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
cd ..
openspec validate --all --strict --no-interactive
```

Expected: all commands exit 0. The ordinary backend suite may skip only explicitly gated migration
and real-model tests described in `AGENTS.md`.

- [ ] **Step 8: Run clean PostgreSQL migration smoke test**

Run Docker, then:

```bash
cd backend
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

Expected: PASS and only `reconcile_migration_test` is recreated.

- [ ] **Step 9: Mark OpenSpec tasks complete and commit**

Mark each verified checkbox in `openspec/changes/add-chat-api-connectors/tasks.md`, then:

```bash
git add backend frontend AGENTS.md \
  openspec/changes/add-chat-api-connectors/tasks.md
git commit -m "test: verify chat-driven API connector flow"
```

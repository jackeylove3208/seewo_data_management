# Conversation Remote CSV Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a public HTTPS CSV link sent only in the Agent conversation to become a safe,
immutable third-party authoritative snapshot without exposing the URL to models or enabling remote
sources in manual synchronization.

**Architecture:** The conversation endpoint deterministically registers and redacts one link, then
passes only a conversation-bound resource reference to the existing intent model. A new
`agent-sync-graph-v2` materialization node uses a connection-pinned downloader to publish a
`SourceFile` before existing CSV inspection and normalization. Ambiguous remote schemas use a
versioned read-only Skill over evidence-manifest-bound MCP resources.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy asyncio, Alembic, httpx/httpcore,
Polars CSV inspection, pytest, React, TypeScript, Vitest, Playwright, OpenSpec.

## Global Constraints

- The capability is activated only by a URL in
  `POST /api/agent/conversations/{conversation_id}/messages`.
- `/api/agent/tasks` and the manual-sync UI do not accept or expose remote sources.
- First release accepts one public HTTPS direct CSV link per message; no HTTP, authentication,
  browser scraping, API, Excel, JSON, archive, cookie, custom header, or scheduled refresh.
- Full URLs and query strings never enter model inputs, MCP arguments, displayed chat history,
  reports, client errors, or ordinary logs.
- Every redirect and actual TCP destination must pass public-address policy.
- Existing `max_upload_bytes` is the remote download byte limit.
- Remote content is third-party authoritative and read-only.
- Entity types remain department, student, and teacher; fields remain `category`, `name`, `number`,
  `class_name`, `phone`, and `email`.
- Existing tasks retain `agent-sync-graph-v1`; only remote tasks use `agent-sync-graph-v2`.
- Every behavior change follows RED → GREEN → REFACTOR and every OpenSpec task is checked off when
  its verification passes.

---

### Task 1: Remote-source persistence and contracts

**Files:**
- Create: `backend/app/models/remote_sources.py`
- Create: `backend/app/remote_sources/__init__.py`
- Create: `backend/app/remote_sources/repository.py`
- Create: `backend/alembic/versions/0037_conversation_remote_sources.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/schemas/agent_api.py`
- Modify: `backend/app/schemas/agent_conversation.py`
- Test: `backend/tests/unit/models/test_remote_source_models.py`
- Test: `backend/tests/unit/schemas/test_agent_contracts.py`
- Test: `backend/tests/integration/test_migrations.py`

**Interfaces:**
- Produces `RemoteSourceRecord` with `id`, `tenant_id`, `created_by`, `conversation_id`, nullable
  `task_id`/`source_file_id`, `original_url`, `display_origin`, lifecycle state, retrieval facts,
  and timestamps.
- Produces `RemoteSourceRepository.register(...)`, `list_for_conversation(...)`,
  `bind_to_task(...)`, `mark_materializing(...)`, `mark_ready(...)`, and `mark_failed(...)`.
- Extends `AgentConnectorSelection` with `kind="remote_csv"` and `remote_source_id`.
- Extends conversation context/decision with `available_remote_sources` and `remote_source_id`.

- [x] **Step 1: Write failing model and contract tests**

```python
def test_remote_csv_connector_requires_only_remote_source_id() -> None:
    remote_id = uuid4()
    selection = AgentConnectorSelection(
        kind="remote_csv",
        remote_source_id=remote_id,
    )
    assert selection.remote_source_id == remote_id
    with pytest.raises(ValueError, match="remote CSV connector"):
        AgentConnectorSelection(
            kind="remote_csv",
            remote_source_id=remote_id,
            source_ref="seewo/current.csv",
        )


async def test_remote_source_repository_is_conversation_scoped(session) -> None:
    first = await RemoteSourceRepository(session).register(
        tenant_id="school-1",
        created_by="operator-1",
        conversation_id=uuid4(),
        original_url="https://public.example/roster.csv?token=private",
        display_origin="public.example",
    )
    assert first.state == "registered"
    assert first.task_id is None
    assert first.source_file_id is None
```

- [x] **Step 2: Run tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/models/test_remote_source_models.py \
  tests/unit/schemas/test_agent_contracts.py -q
```

Expected: collection/import failure because remote-source model/repository and connector fields do
not exist.

- [x] **Step 3: Implement the minimal persistence and Pydantic contracts**

Use these exact public shapes:

```python
class ConversationRemoteSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    remote_source_id: UUID
    display_origin: str = Field(min_length=1, max_length=255)


class AgentConnectorSelection(BaseModel):
    kind: Literal["csv", "api", "database", "local", "remote_csv"]
    upload_id: UUID | None = None
    configuration_id: str | None = None
    source_ref: str | None = None
    remote_source_id: UUID | None = None
```

Use database states `registered`, `materializing`, `ready`, and `failed`; add unique indexes for a
non-null `task_id` and `source_file_id`, and ordinary indexes for tenant, creator, and conversation.
Add settings:

```python
conversation_remote_csv_enabled: bool = False
remote_source_max_redirects: int = Field(default=3, ge=0, le=5)
remote_source_connect_timeout_seconds: PositiveFloat = 10
remote_source_read_timeout_seconds: PositiveFloat = 30
remote_source_total_timeout_seconds: PositiveFloat = 60
```

- [x] **Step 4: Add and run migration verification**

Extend the migration integration assertions with:

```python
assert "remote_sources" in inspector.get_table_names()
columns = {column["name"] for column in inspector.get_columns("remote_sources")}
assert {
    "conversation_id",
    "task_id",
    "source_file_id",
    "original_url",
    "display_origin",
    "state",
    "content_sha256",
    "safe_problem_code",
} <= columns
```

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/models/test_remote_source_models.py \
  tests/unit/schemas/test_agent_contracts.py \
  tests/integration/test_migrations.py -q
```

Expected: unit tests pass; PostgreSQL-only smoke test is skipped unless its dedicated URL is set.

- [x] **Step 5: Commit Task 1**

```bash
git add backend/app/models/remote_sources.py backend/app/remote_sources \
  backend/app/models/__init__.py backend/app/core/config.py \
  backend/app/schemas/agent_api.py backend/app/schemas/agent_conversation.py \
  backend/alembic/versions/0037_conversation_remote_sources.py \
  backend/tests/unit/models/test_remote_source_models.py \
  backend/tests/unit/schemas/test_agent_contracts.py \
  backend/tests/integration/test_migrations.py
git commit -m "feat: add conversation remote source records"
```

### Task 2: Conversation-only link registration and redaction

**Files:**
- Create: `backend/app/remote_sources/links.py`
- Modify: `backend/app/api/routes/agent.py`
- Modify: `backend/app/ai/conversation_agent.py`
- Modify: `backend/app/ai/skills/converse-school-data-sync/SKILL.md`
- Test: `backend/tests/unit/remote_sources/test_links.py`
- Test: `backend/tests/unit/ai/test_conversation_agent.py`
- Test: `backend/tests/integration/api/test_agent_api.py`

**Interfaces:**
- Produces `extract_conversation_link(message: str) -> ExtractedConversationLink | None`.
- Produces `redact_conversation_links(text: str) -> str`.
- Conversation model sees `ConversationRemoteSource` facts and can return `remote_source_id`.

- [x] **Step 1: Write failing deterministic link tests**

```python
def test_extracts_one_https_link_and_redacts_query() -> None:
    extracted = extract_conversation_link(
        "请同步 https://data.example.test/roster.csv?secret=value 的学生"
    )
    assert extracted is not None
    assert extracted.original_url.endswith("?secret=value")
    assert extracted.display_origin == "data.example.test"
    assert extracted.redacted_message == (
        "请同步 [远程CSV来源:data.example.test] 的学生"
    )


@pytest.mark.parametrize(
    "message,code",
    [
        ("http://data.example.test/a.csv", "remote_source_https_required"),
        ("https://user:pass@data.example.test/a.csv", "remote_source_credentials_forbidden"),
        ("https://a.example/a.csv https://b.example/b.csv", "remote_source_multiple_links"),
    ],
)
def test_rejects_unsafe_or_multiple_links(message: str, code: str) -> None:
    with pytest.raises(RemoteSourceRegistrationError) as raised:
        extract_conversation_link(message)
    assert raised.value.code == code
```

- [x] **Step 2: Run tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/remote_sources/test_links.py -q
```

Expected: import failure because `links.py` does not exist.

- [x] **Step 3: Implement deterministic extraction**

Implement URL tokenization with `urllib.parse.urlsplit`, reject multiple tokens and fragments that
cannot be normalized, require `https`, reject `username`, `password`, IP-literal hosts, missing
hosts, control characters, and ports outside `1..65535`. Return only the original private URL,
normalized hostname/port origin, and redacted message. Do not issue DNS or HTTP during registration.

- [x] **Step 4: Write failing conversation boundary tests**

Add a provider that captures its request and returns:

```python
{
    "result": {
        "kind": "start_confirmation",
        "title": "远程学生同步",
        "entity_types": ["student"],
        "remote_source_id": str(remote_source_id),
        "target_ref": "seewo/roster.csv",
        "message_zh": "已确认远程权威来源和希沃目标。",
    }
}
```

Assert:

```python
assert "secret=value" not in provider.requests[0].messages[1].content
assert "https://" not in provider.requests[0].messages[1].content
assert "[远程CSV来源:data.example.test]" in provider.requests[0].messages[1].content
assert persisted_user_message.text == "请同步 [远程CSV来源:data.example.test] 的学生"
assert response.json()["intent"]["source"] == {
    "kind": "remote_csv",
    "remote_source_id": str(remote_source_id),
}
```

Also assert no-link messages create zero `RemoteSourceRecord` rows, multiple links return `422`
without a provider request, and a provider-invented remote ID becomes clarification.

- [x] **Step 5: Run the boundary tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/ai/test_conversation_agent.py \
  tests/integration/api/test_agent_api.py -k "remote_source or conversation_link" -q
```

Expected: failures because the route sends/stores the original message and the decision schema has no
remote source.

- [x] **Step 6: Integrate registration before persistence/model invocation**

In `send_agent_message`, while the conversation lock is held:

```python
extracted = extract_conversation_link(body.message)
safe_message = extracted.redacted_message if extracted else body.message
remote = (
    await RemoteSourceRepository(session).register(
        tenant_id=operator.tenant_id,
        created_by=operator.operator_id,
        conversation_id=conversation.id,
        original_url=extracted.original_url,
        display_origin=extracted.display_origin,
    )
    if extracted and settings.conversation_remote_csv_enabled
    else None
)
```

Persist `safe_message`, redact every legacy history message again before model construction, and
pass server-loaded conversation remote facts. Validate `decision.remote_source_id` against those
facts and construct `{"kind": "remote_csv", "remote_source_id": ...}` only for a listed reference.
Update the conversation Skill to choose a remote authoritative source only from
`available_remote_sources`, never from message text.

- [x] **Step 7: Run tests and commit Task 2**

```bash
cd backend
.venv/bin/pytest tests/unit/remote_sources/test_links.py \
  tests/unit/ai/test_conversation_agent.py \
  tests/integration/api/test_agent_api.py -q
cd ..
git add backend/app/remote_sources/links.py backend/app/api/routes/agent.py \
  backend/app/ai/conversation_agent.py \
  backend/app/ai/skills/converse-school-data-sync/SKILL.md \
  backend/tests/unit/remote_sources/test_links.py \
  backend/tests/unit/ai/test_conversation_agent.py \
  backend/tests/integration/api/test_agent_api.py
git commit -m "feat: register remote CSV links from conversations"
```

### Task 3: Task binding and manual-entry isolation

**Files:**
- Modify: `backend/app/agent_runtime/task_service.py`
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/integration/api/test_agent_api.py`
- Test: `backend/tests/integration/agent_runtime/test_supervisor.py`

**Interfaces:**
- `AgentTaskService.create(..., conversation_id)` accepts
  `remote_csv + local` only with a matching remote conversation record.
- Manual `/api/agent/tasks` rejects `remote_csv` before persistence and lock acquisition.
- Task creation binds the local target snapshot immediately and reserves the remote record for
  later materialization.

- [x] **Step 1: Write failing task-isolation tests**

```python
def test_manual_api_rejects_remote_csv_before_task_or_lock(client) -> None:
    response = client.post(
        "/api/agent/tasks",
        headers={"Idempotency-Key": "forged-remote"},
        json={
            "title": "forged",
            "entity_types": ["student"],
            "source": {"kind": "remote_csv", "remote_source_id": str(uuid4())},
            "target": {"kind": "local", "source_ref": "seewo/roster.csv"},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "manual_csv_only"
    assert client.get("/api/agent/active-lock").json()["active"] is False
```

Add service tests for cross-tenant, cross-operator, cross-conversation, already-bound, and
`remote_csv + database` rejection.

- [x] **Step 2: Run tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_agent_api.py \
  tests/integration/agent_runtime/test_supervisor.py -k "remote_csv or forged_remote" -q
```

Expected: schema or capability failures do not yet have the required conversation-bound semantics.

- [x] **Step 3: Implement the conversation-only service guard**

Change `_validate_connector_runtime` to receive `conversation_id` and accept:

```python
if source_kind == "remote_csv" and target_kind == "local":
    if conversation_id is None:
        raise AgentConnectorCapabilityFailure(
            "remote CSV is available only from an Agent conversation"
        )
    return
```

Before creating `ReconciliationTask`, load the remote record with tenant/operator/conversation
criteria. After task flush, bind the record to the task and bind only the local target as a target
`SourceFile`/`Snapshot`. Do not create an authoritative placeholder.

Keep the manual route's existing explicit CSV-only precheck and add `remote_csv` to its rejected
cases so its public error remains `manual_csv_only`.

- [x] **Step 4: Run tests and commit Task 3**

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_agent_api.py \
  tests/integration/agent_runtime/test_supervisor.py -q
cd ..
git add backend/app/agent_runtime/task_service.py backend/app/api/routes/agent.py \
  backend/tests/integration/api/test_agent_api.py \
  backend/tests/integration/agent_runtime/test_supervisor.py
git commit -m "feat: bind remote sources only to conversation tasks"
```

### Task 4: Connection-pinned HTTPS CSV downloader and materializer

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements-ci.txt`
- Create: `backend/app/remote_sources/network.py`
- Create: `backend/app/remote_sources/materializer.py`
- Test: `backend/tests/unit/remote_sources/test_network.py`
- Test: `backend/tests/integration/remote_sources/test_materializer.py`

**Interfaces:**
- `resolve_public_addresses(host: str, port: int) -> tuple[str, ...]`.
- `PinnedNetworkBackend` delegates TCP connection to an approved address while TLS keeps the
  original hostname.
- `RemoteCsvDownloader.download(url, destination) -> DownloadedRemoteCsv`.
- `RemoteSourceMaterializer.materialize(session, task_id, remote_source_id) -> SourceFile`.

- [x] **Step 1: Write failing network policy tests**

Cover `127.0.0.1`, `10.0.0.1`, `169.254.169.254`, `::1`, link-local, reserved, multicast, IP
literals, DNS answers containing no global address, mixed answers, HTTPS downgrade, fourth redirect,
timeout, `Content-Length` overflow, streamed overflow, empty body, HTML, JSON, XLSX/ZIP signatures,
and malformed CSV.

Representative tests:

```python
@pytest.mark.parametrize("value", ["127.0.0.1", "10.0.0.1", "::1", "169.254.169.254"])
def test_rejects_non_global_destination(value: str) -> None:
    with pytest.raises(RemoteSourceFailure) as raised:
        require_public_address(value)
    assert raised.value.code == "remote_source_dns_rejected"


async def test_redirect_is_revalidated() -> None:
    downloader = RemoteCsvDownloader(
        resolver=FakeResolver({"public.example": ("203.0.113.10",)}),
        transport=SequenceTransport(
            [redirect("https://private.example/a.csv"), csv_response()]
        ),
        max_redirects=3,
        max_bytes=1024,
    )
    with pytest.raises(RemoteSourceFailure, match="remote_source_dns_rejected"):
        await downloader.download("https://public.example/a.csv", destination)
```

Use globally routable documentation substitutes through an injected policy in tests; do not access
the internet.

- [x] **Step 2: Run network tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/unit/remote_sources/test_network.py -q
```

Expected: import failure because the network module does not exist.

- [x] **Step 3: Implement policy, pinned transport, and bounded streaming**

Declare `httpcore>=1,<2` directly. Build `PinnedNetworkBackend` around
`httpcore.AnyIOBackend`; in `connect_tcp(host, port, ...)`, call the injected resolver, reject
non-global answers, and delegate using the selected IP. `start_tls` remains on the returned stream,
so httpcore passes the original hostname for SNI.

Use an `httpcore.AsyncConnectionPool`-backed `httpx.AsyncBaseTransport`, `trust_env=False`,
`follow_redirects=False`, a `Timeout` with configured connect/read values, and an outer total
timeout. Stream into an `xb` temporary path, hash as bytes arrive, stop above
`settings.max_upload_bytes`, and unlink on every exception. Run `inspect_csv` before returning a
successful `DownloadedRemoteCsv`.

- [x] **Step 4: Write failing materializer persistence/idempotency tests**

Assert a successful materialization creates exactly one authoritative `SourceFile` and `Snapshot`,
sets `RemoteSourceRecord.state == "ready"`, stores hash/bytes/type/time, and never stores the full URL
in source names or checkpoint payloads. Repeating it returns the same file without another transport
request. A failed download sets only a safe code and creates no authoritative file/snapshot.

- [x] **Step 5: Implement and verify the materializer**

Use a deterministic final storage name based on remote-source ID and content hash, publish database
rows in one transaction after the file is complete, and use the existing `_agent_snapshot` semantics
with `mapping_version="agent-csv-v2"`.

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/remote_sources/test_network.py \
  tests/integration/remote_sources/test_materializer.py -q
```

Expected: all remote network and materializer tests pass with no real network.

- [x] **Step 6: Commit Task 4**

```bash
git add backend/pyproject.toml backend/requirements-ci.txt \
  backend/app/remote_sources/network.py backend/app/remote_sources/materializer.py \
  backend/tests/unit/remote_sources/test_network.py \
  backend/tests/integration/remote_sources/test_materializer.py
git commit -m "feat: materialize public remote CSV safely"
```

### Task 5: `agent-sync-graph-v2` materialization action

**Files:**
- Modify: `backend/app/agent_graph/definition.py`
- Modify: `backend/app/agent_graph/runtime.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Modify: `backend/app/agent_runtime/service.py`
- Modify: `backend/app/agent_graph/worker.py`
- Test: `backend/tests/unit/agent_graph/test_definition.py`
- Test: `backend/tests/unit/agent_graph/test_runtime.py`
- Test: `backend/tests/integration/agent_graph/test_production_runtime.py`
- Test: `backend/tests/integration/agent_graph/test_worker.py`

**Interfaces:**
- Adds persisted graph version `agent-sync-graph-v2`.
- Adds node/action `materialize_sources` / `materialize_remote_authority`.
- Only tasks whose authoritative intent kind is `remote_csv` select graph v2.

- [x] **Step 1: Write failing graph definition and routing tests**

```python
def test_sync_graph_v2_materializes_before_inspection() -> None:
    graph = get_graph_definition("agent-sync-graph-v2")
    assert {
        (item.action_kind, item.successor_node)
        for item in graph.node("acquire_school_lock").action_templates
    } == {("materialize_sources", "materialize_sources")}
    assert {
        (item.action_kind, item.successor_node)
        for item in graph.node("materialize_sources").action_templates
    } == {("materialize_remote_authority", "inspect_sources")}


def test_sync_v2_templates_are_not_rollback_templates() -> None:
    templates = production_candidate_templates(
        "materialize_sources",
        graph_version="agent-sync-graph-v2",
    )
    assert [item.action_id for item in templates] == ["materialize_remote_authority"]
```

Add a supervisor test showing a remote task creates graph v2 at cursor 2/current node
`materialize_sources`; a local task remains sync v1/current node `inspect_sources`.

- [x] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/unit/agent_graph/test_definition.py \
  tests/unit/agent_graph/test_runtime.py \
  tests/integration/agent_runtime/test_supervisor.py -q
```

Expected: unsupported graph version and missing node/action failures.

- [x] **Step 3: Implement graph version and exhaustive template routing**

Create `SYNC_GRAPH_V2` by copying the reviewed v1 nodes and replacing only the lock successor plus
the new deterministic node. Register all three versions explicitly:

```python
if graph_version in {"agent-sync-graph-v1", "agent-sync-graph-v2"}:
    templates = _SYNC_TEMPLATES_V2 if graph_version.endswith("-v2") else _SYNC_TEMPLATES
elif graph_version == "agent-rollback-graph-v1":
    templates = _ROLLBACK_TEMPLATES
else:
    raise ValueError(f"unsupported Agent graph version: {graph_version}")
```

In `AgentSupervisorService.start`, select sync v2 only when task intent source kind is
`remote_csv`; create the graph state and transition from lock to `materialize_sources`. Keep the
existing workflow version `agent-graph-v1`.

- [x] **Step 4: Write failing executor and recovery tests**

Seed a remote task and fake materializer. Assert the action:

```python
assert outcome.action_id == "materialize_remote_authority"
assert source.source_role == "authoritative"
assert graph.current_node == "inspect_sources"
assert transition.action_id == "materialize_remote_authority"
```

Replay the same cursor/action after simulating an interrupted transition and assert one materializer
publication and one authoritative snapshot. Assert a typed materialization failure produces no
inspection transition and records no raw URL.

- [x] **Step 5: Implement executor integration and verify**

Route `materialize_remote_authority` before source inspection in
`ProductionGraphActionExecutor.__call__`. Build its manifest from
`remote-source:<remote_source_id>` and record only resource ID, safe origin, hash, byte count, and
safe code. Inject the materializer through the executor constructor so tests never use real DNS.

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/agent_graph/test_definition.py \
  tests/unit/agent_graph/test_runtime.py \
  tests/integration/agent_graph/test_production_runtime.py \
  tests/integration/agent_graph/test_worker.py -q
```

- [x] **Step 6: Commit Task 5**

```bash
git add backend/app/agent_graph/definition.py backend/app/agent_graph/runtime.py \
  backend/app/agent_graph/production_executor.py backend/app/agent_runtime/service.py \
  backend/app/agent_graph/worker.py backend/tests/unit/agent_graph/test_definition.py \
  backend/tests/unit/agent_graph/test_runtime.py \
  backend/tests/integration/agent_graph/test_production_runtime.py \
  backend/tests/integration/agent_graph/test_worker.py
git commit -m "feat: add remote source materialization graph"
```

### Task 6: Evidence-bounded remote source-understanding Skill

**Files:**
- Create: `backend/app/ai/skills/understand-remote-organization-source/SKILL.md`
- Create: `backend/app/ai/skills/understand-remote-organization-source/agents/openai.yaml`
- Modify: `backend/app/agent_graph/runtime.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Modify: `backend/app/agent_graph/tools.py`
- Test: `backend/tests/unit/ai/test_agent_skill_content.py`
- Test: `backend/tests/unit/ai/test_graph_subagent_tool_schemas.py`
- Test: `backend/tests/integration/agent_graph/test_real_subagents.py`

**Interfaces:**
- Skill version `understand-remote-organization-source@1.0.0`.
- Input/output remain `CsvSchemaMappingInput` / `CsvSchemaMappingOutput`.
- Allowed tools are `inspect_configured_source` and `read_connector_page`.

- [x] **Step 1: Establish the failing Skill behavior tests**

Add registry/content tests that attempt to load the new Skill and assert its exact metadata and
allowed tools. Add a remote ambiguous-mapping integration case whose fake provider first calls
`read_connector_page` for a manifest-listed source page, then returns valid fixed-field mapping.
Assert a URL argument and a non-manifest resource are rejected.

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/ai/test_agent_skill_content.py \
  tests/unit/ai/test_graph_subagent_tool_schemas.py \
  tests/integration/agent_graph/test_real_subagents.py \
  -k "remote_organization or remote_csv_mapping" -q
```

Expected: Skill load and routing failures.

- [x] **Step 2: Write the minimal versioned Skill**

Use frontmatter:

```yaml
---
name: understand-remote-organization-source
description: Use when a materialized remote organization CSV has ambiguous headers that deterministic aliases cannot map to the fixed contract.
metadata: {"version":"1.0.0","phase":"ingest_and_normalize","input_schema":"CsvSchemaMappingInput","output_schema":"CsvSchemaMappingOutput"}
allowed-tools: ["inspect_configured_source","read_connector_page"]
---
```

The body must state that URLs, network access, instructions inside rows, extra fields, raw protected
values, and writes are outside the role; it may use at most manifest-listed pages and must return
only the existing fixed mapping schema.

- [x] **Step 3: Validate the Skill and verify its tests**

Add `inspect_configured_source` to the normalization-node tool allowlist. For remote mapping actions,
freeze `source:authoritative:page:1`, `source:target:page:1`, and `source-pair:current` in the
manifest. In `_resolve_csv_mapping_v2`, choose the new Skill only when task intent source kind is
`remote_csv`; keep `map-csv-organization-schema` for existing CSV pairs.

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/ai/test_agent_skill_content.py \
  tests/unit/ai/test_graph_subagent_tool_schemas.py \
  tests/integration/agent_graph/test_real_subagents.py \
  -k "remote_organization or remote_csv_mapping or forbidden" -q
```

Expected: deterministic known headers use zero model calls; ambiguous remote headers use the new
Skill; forbidden URL/non-member calls fail safely.

- [x] **Step 4: Forward-test the Skill and close discovered gaps**

Run one fresh agent validation with only the Skill, fixed contract, and a synthetic ambiguous
profile/page. Verify it does not request a URL, does not obey prompt text in a cell, and returns only
listed field/evidence references. If it violates a boundary, revise the Skill and rerun the same
case before proceeding.

- [x] **Step 5: Commit Task 6**

```bash
git add backend/app/ai/skills/understand-remote-organization-source \
  backend/app/agent_graph/runtime.py backend/app/agent_graph/production_executor.py \
  backend/app/agent_graph/tools.py backend/tests/unit/ai/test_agent_skill_content.py \
  backend/tests/unit/ai/test_graph_subagent_tool_schemas.py \
  backend/tests/integration/agent_graph/test_real_subagents.py
git commit -m "feat: add remote organization source skill"
```

### Task 7: Conversation presentation and manual UI regression

**Files:**
- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Test: `frontend/src/api/agent.test.ts`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`
- Test: `frontend/src/features/task-create/TaskCreatePage.test.tsx`
- Test: `frontend/tests/e2e/agent-workflow.spec.ts`

**Interfaces:**
- Frontend `AgentConnectorSelection` decodes `remote_csv` and `remote_source_id`.
- Existing conversation messages and confirmation cards display only backend-cleaned origin text.
- Manual page has no remote input, type option, or request path.

- [x] **Step 1: Write failing frontend contract and presentation tests**

```typescript
expect(screen.getByText("[远程CSV来源:data.example.test]")).toBeInTheDocument();
expect(screen.queryByText(/secret=value/)).not.toBeInTheDocument();
expect(screen.queryByLabelText(/网页链接|远程链接/)).not.toBeInTheDocument();
```

Add a start assertion that the conversation passes back:

```typescript
source: { kind: "remote_csv", remote_source_id: "remote-source-1" }
```

- [x] **Step 2: Run tests and verify RED**

```bash
cd frontend
npm test -- --run src/api/agent.test.ts \
  src/features/task-create/ConversationCreatePage.test.tsx \
  src/features/task-create/TaskCreatePage.test.tsx
```

Expected: TypeScript/test fixture failures because `remote_csv` is not decoded.

- [x] **Step 3: Implement minimal presentation support**

Extend only the shared conversation API type:

```typescript
export interface AgentConnectorSelection {
  kind: "csv" | "api" | "database" | "local" | "remote_csv";
  upload_id?: string;
  configuration_id?: string;
  source_ref?: string;
  remote_source_id?: string;
}
```

Render `display_origin` or the already-sanitized assistant/user text in the existing confirmation
surface. Do not add link extraction, registration requests, manual form controls, or manual
connector options.

- [x] **Step 4: Run frontend tests and commit Task 7**

```bash
cd frontend
npm test -- --run src/api/agent.test.ts \
  src/features/task-create/ConversationCreatePage.test.tsx \
  src/features/task-create/TaskCreatePage.test.tsx
npm run typecheck
cd ..
git add frontend/src/api/agent.ts \
  frontend/src/features/task-create/ConversationCreatePage.tsx \
  frontend/src/api/agent.test.ts \
  frontend/src/features/task-create/ConversationCreatePage.test.tsx \
  frontend/src/features/task-create/TaskCreatePage.test.tsx \
  frontend/tests/e2e/agent-workflow.spec.ts
git commit -m "feat: present remote CSV conversation intent"
```

### Task 8: Documentation, full verification, and OpenSpec completion

**Files:**
- Modify: `docs/api/connectors.md`
- Modify: `backend/app/agent_runtime/README.md`
- Modify: `openspec/changes/add-conversation-remote-csv-ingestion/tasks.md`
- Modify: `docs/superpowers/plans/2026-07-28-conversation-remote-csv-ingestion.md`

**Interfaces:**
- Documents the feature flag, conversation-only trigger, public HTTPS direct-CSV limits, Graph v2,
  safe errors, and manual rejection.

- [x] **Step 1: Update operator and runtime documentation**

Document exact settings and commands, including:

```text
RECONCILIATION_CONVERSATION_REMOTE_CSV_ENABLED=true
```

State that the full URL is private, manual sync remains upload-only, remote content is fetched once
after task confirmation, and safe errors are separated into DNS, redirect, timeout, size, content,
and CSV parse failures.

- [x] **Step 2: Run backend quality gates**

```bash
cd backend
.venv/bin/python -m pip install --constraint requirements-ci.txt -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

Expected: all tests pass except the documented dedicated PostgreSQL migration smoke test skip when
its environment variable is absent; Ruff and mypy report zero errors.

- [x] **Step 3: Run the clean PostgreSQL migration smoke test**

```bash
docker compose -f infra/docker-compose.yml up -d
cd backend
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

Expected: one passing migration smoke test; it recreates and removes only
`reconcile_migration_test`.

- [x] **Step 4: Run frontend and OpenSpec quality gates**

```bash
cd frontend
npm ci
npm test -- --run
npm run lint
npm run typecheck
npm run build
npm run test:e2e
cd ..
openspec validate --all --strict --no-interactive
git diff --check
```

Expected: all commands exit zero.

- [x] **Step 5: Mark OpenSpec tasks complete and commit verification/docs**

Change every completed checkbox in
`openspec/changes/add-conversation-remote-csv-ingestion/tasks.md` to `[x]`, record any intentionally
deferred item as a separate explicit follow-up rather than marking it complete, then run:

```bash
git add docs/api/connectors.md backend/app/agent_runtime/README.md \
  openspec/changes/add-conversation-remote-csv-ingestion/tasks.md \
  docs/superpowers/plans/2026-07-28-conversation-remote-csv-ingestion.md
git commit -m "docs: complete conversation remote CSV ingestion"
```

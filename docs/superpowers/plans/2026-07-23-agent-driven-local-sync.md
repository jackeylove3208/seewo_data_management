# Agent-driven local synchronization implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make local-source synchronization and the new-conversation entry point use a real, versioned supervisor/sub-agent model workflow while retaining server-owned security, durable tasks, approvals, execution and audit guarantees.

**Architecture:** A conversation supervisor uses the existing OpenAI-compatible gateway and a strict `ConversationAgentDecision` schema to discover approved local sources and create a confirmation. A server-only local-source MCP façade enforces configured roots and gives the ingestion Agent bounded source pages. Ingestion, reconciliation, governance, report and rollback are each bound to complete versioned Skills; their model outputs are schema- and evidence-validated before existing durable handlers persist or execute anything.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Pydantic v2, httpx, pytest, React, TypeScript, TanStack Query, Vitest.

## Global constraints

- `OperatorContext.tenant_id` is backend-owned; clients never submit or override it.
- The demo has no authentication, school picker or role management.
- Local reads are limited to canonical configured roots; no Agent gets shell, SQL, arbitrary path, credentials, direct third-party writes, or direct target writes.
- All data, filenames, paths and user messages are untrusted evidence, never executable instructions.
- Phone values are tokenized before an Agent receives student evidence.
- Every model request uses a versioned Skill, strict JSON schema, bounded evidence and exactly one initial request plus at most three retries.
- Correct records produce no findings or workbench rows.
- Persisted state, not browser state or model prose, controls phase, locks, approvals, execution and rollback.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/app/core/config.py` | Defines and validates canonical local-source roots. |
| `backend/app/local_sources/service.py` | Resolves safe relative paths, blocks escape/excluded files, lists and pages approved local sources. |
| `backend/app/ai/conversation_agent.py` | Builds and validates the model-backed supervisor conversation decision. |
| `backend/app/schemas/agent_conversation.py` | Strict conversation intent, source reference, clarification and confirmation schemas. |
| `backend/app/ai/agent_prompting.py` | Builds one model message set from a versioned Skill and shared safety contract. |
| `backend/app/ai/skills/*/SKILL.md` | Complete Agent identities, duties, stop conditions and phase rules. |
| `backend/app/agent_runtime/local_ingestion_handlers.py` | Runs local-source inspection/paging plus ingestion Agent normalization, then persists validated evidence. |
| `backend/app/api/routes/agent.py` | Replaces keyword intent with the supervisor Agent; makes history task/run based. |
| `frontend/src/features/task-create/ConversationCreatePage.tsx` | Renders real supervisor responses, local-source confirmations and safe Chinese progress. |

## Task 1: Safe local-source configuration and reader façade

**Files:**

- Create: `backend/app/local_sources/__init__.py`
- Create: `backend/app/local_sources/service.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/local_sources/test_service.py`

**Interfaces:**

- Produces `LocalSourceService(settings).list_sources()`, `.inspect(relative_ref)`, and `.read_page(relative_ref, offset, limit=50)`.
- Produces `LocalSourceSummary`, `LocalSourceInspection`, and `LocalSourcePage` values containing relative source references only.
- Raises `LocalSourceAccessError(code)` for every blocked request.

- [ ] **Step 1: Write the failing containment and paging tests**

```python
async def test_read_page_rejects_parent_escape(tmp_path: Path, settings: Settings) -> None:
    settings.agent_local_read_roots = (tmp_path / "allowed",)
    service = LocalSourceService(settings)
    with pytest.raises(LocalSourceAccessError, match="outside_allowed_roots"):
        await service.read_page("../secret.csv", offset=0, limit=50)

async def test_read_page_returns_at_most_fifty_records(tmp_path: Path, settings: Settings) -> None:
    source = write_synthetic_roster(tmp_path / "allowed" / "third-party" / "roster.csv", 51)
    settings.agent_local_read_roots = (source.parents[1],)
    page = await LocalSourceService(settings).read_page("third-party/roster.csv", offset=0, limit=99)
    assert len(page.records) == 50
    assert page.next_offset == 50
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/unit/local_sources/test_service.py -q`

Expected: FAIL because `app.local_sources.service` does not exist.

- [ ] **Step 3: Write the minimum safe implementation**

```python
class Settings(BaseSettings):
    agent_local_read_roots: tuple[Path, ...] = ()

class LocalSourceService:
    async def read_page(self, relative_ref: str, *, offset: int, limit: int = 50) -> LocalSourcePage:
        path = self._resolve(relative_ref)
        return self._reader_for(path).read_page(path, offset=offset, limit=min(max(limit, 1), 50))
```

Implement `_resolve()` with `Path.resolve(strict=True)`, root containment, regular-file checks, symlink escape checks, and a denylist for environment, credential, source-control and source-code files. Return stable codes `outside_allowed_roots`, `source_not_found`, and `unsupported_source`. Do not execute file contents.

- [ ] **Step 4: Run focused verification**

Run: `cd backend && .venv/bin/pytest tests/unit/local_sources/test_service.py -q && .venv/bin/ruff check app/local_sources app/core/config.py && .venv/bin/mypy app/local_sources app/core/config.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/app/local_sources backend/tests/unit/local_sources
git commit -m "feat: add safe local source reader"
```

## Task 2: Real conversation supervisor Agent and complete Skill contracts

**Files:**

- Create: `backend/app/schemas/agent_conversation.py`
- Create: `backend/app/ai/conversation_agent.py`
- Create: `backend/app/ai/agent_prompting.py`
- Create: `backend/app/ai/skills/converse-school-data-sync/SKILL.md`
- Create: `backend/app/ai/skills/discover-local-data-source/SKILL.md`
- Modify: all existing Agent `SKILL.md` files for supervisor, ingestion, analysis, governance, reporting and rollback.
- Test: `backend/tests/unit/ai/test_conversation_agent.py`
- Test: `backend/tests/unit/ai/test_agent_skills.py`

**Interfaces:**

- Produces `ConversationSupervisorAgent.reply(context) -> ConversationAgentDecision`.
- `ConversationAgentDecision.kind` is exactly `clarification`, `intent_update`, `start_confirmation`, `active_task_notice`, or `safe_failure`.
- `build_agent_request(skill, input_payload, output_model)` is the common model request builder.

- [ ] **Step 1: Write failing supervisor tests**

```python
async def test_supervisor_uses_versioned_skill_and_returns_confirmation() -> None:
    provider = ScriptedProvider({"result": {
        "kind": "start_confirmation", "title": "七年级同步",
        "entity_types": ["student"], "source_ref": "third-party/a.csv",
        "target_ref": "seewo/b.csv", "message_zh": "已确认数据来源。"
    }})
    decision = await ConversationSupervisorAgent(provider, SkillRegistry()).reply(context())
    assert decision.kind == "start_confirmation"
    assert "converse-school-data-sync" in provider.requests[0].messages[0].content

async def test_supervisor_rejects_source_not_returned_by_tool() -> None:
    decision = await agent.reply(context(available_source_refs=("third-party/a.csv",)))
    assert decision.source_ref in {None, "third-party/a.csv"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/unit/ai/test_conversation_agent.py tests/unit/ai/test_agent_skills.py -q`

Expected: FAIL because the conversation Agent and schemas do not exist.

- [ ] **Step 3: Implement strict schemas, common prompt builder and full Skill texts**

```python
class ConversationAgentDecision(StrictContract):
    kind: Literal["clarification", "intent_update", "start_confirmation", "active_task_notice", "safe_failure"]
    message_zh: str = Field(min_length=1, max_length=1000)
    title: str | None = None
    entity_types: tuple[AgentEntityType, ...] = ()
    source_ref: str | None = None
    target_ref: str | None = None

class ConversationSupervisorAgent:
    async def reply(self, context: ConversationAgentContext) -> ConversationAgentDecision:
        skill = self._skills.load("converse-school-data-sync", "1.0.0")
        response = await self._provider.complete_json_once(
            build_agent_request(skill, context.model_dump(mode="json"), ConversationAgentDecision)
        )
        return validate_conversation_decision(response.output, context.available_source_refs)
```

Every Skill must contain the common safety contract from the approved design, then: identity, inputs, allowed tools, duties, prohibited actions, exact JSON output and stop conditions. Registry tests must parse every changed Skill and prove phase capability compatibility.

- [ ] **Step 4: Run focused verification**

Run: `cd backend && .venv/bin/pytest tests/unit/ai/test_conversation_agent.py tests/unit/ai/test_agent_skills.py -q && .venv/bin/ruff check app/ai app/schemas && .venv/bin/mypy app/ai app/schemas`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai backend/app/schemas backend/tests/unit/ai
git commit -m "feat: add model-backed sync supervisor"
```

## Task 3: Connect the conversation API, task creation and history

**Files:**

- Modify: `backend/app/api/routes/agent.py`
- Modify: `backend/app/schemas/agent_api.py`
- Modify: `backend/app/agent_runtime/task_service.py`
- Modify: `backend/app/agent_runtime/repository.py`
- Test: `backend/tests/integration/api/test_agent_api.py`

**Interfaces:**

- The message endpoint invokes the supervisor and persists validated context.
- Confirmation resolves source references again through `LocalSourceService`, then creates existing `new-agent-v1` task/run.
- History returns all tenant task/runs, including tasks without reports.

- [ ] **Step 1: Add failing API tests**

```python
async def test_confirmation_creates_a_task_visible_in_history(client, scripted_provider):
    conversation = await create_conversation(client)
    response = await client.post(f"/api/agent/conversations/{conversation['id']}/messages",
                                 json={"message": "同步七年级学生"})
    assert response.json()["start_confirmation"] is not None
    task = await start_confirmed_task(client, conversation["id"], response.json())
    history = await client.get("/api/agent/history")
    assert str(task["id"]) in {item["id"] for item in history.json()["items"]}
    assert history.json()["items"][0]["completed_at"] is None

async def test_second_confirmed_task_is_refused_by_school_lock(client):
    await create_running_agent_task(client)
    response = await create_confirmed_agent_task(client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "school_lock_conflict"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd backend && .venv/bin/pytest tests/integration/api/test_agent_api.py -q`

Expected: FAIL because `_merge_conversation_intent` is keyword based and history selects `AgentReportRecord` only.

- [ ] **Step 3: Replace keyword merge and report-only history**

```python
decision = await ConversationSupervisorAgent(provider, SkillRegistry()).reply(
    ConversationAgentContext.from_request(conversation, body.message, sources, active_lock)
)
conversation.context = decision.persisted_context()

page = await AgentRuntimeRepository(session).list_task_history(
    tenant_id=operator.tenant_id, cursor=cursor
)
return AgentHistoryPage(items=tuple(to_history_item(row) for row in page.items), next_cursor=page.next_cursor)
```

The browser may only confirm a stored decision ID. Re-resolve both local source references and source versions before task creation; do not trust posted source or target payloads. Remove `_merge_conversation_intent`.

- [ ] **Step 4: Run focused verification**

Run: `cd backend && .venv/bin/pytest tests/integration/api/test_agent_api.py -q && .venv/bin/ruff check app/api app/agent_runtime && .venv/bin/mypy app/api app/agent_runtime`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/app/agent_runtime backend/app/schemas backend/tests/integration/api
git commit -m "feat: create agent tasks from local conversation sources"
```

## Task 4: Agent-driven local ingestion and observable progress

**Files:**

- Create: `backend/app/agent_runtime/local_ingestion_handlers.py`
- Modify: `backend/app/agent_runtime/csv_analysis_worker.py`
- Modify: `backend/app/agent_runtime/task_service.py`
- Test: `backend/tests/integration/agent_runtime/test_agent_ingestion_handler.py`
- Test: `backend/tests/integration/agent_runtime/test_csv_analysis_worker.py`

**Interfaces:**

- `LocalIngestionPhaseHandler.ingest(run_id)` reads server-selected local pages of at most 50 rows, invokes inspection/normalization Skills, validates output membership and persists immutable inputs/marks.
- Emits `source_discovered`, `source_recognized`, `ingestion_page_persisted`, `ingestion_marked`, or `abnormal_input_report_ready`.

- [ ] **Step 1: Write failing ingestion tests**

```python
async def test_ingestion_persists_only_model_validated_current_page(session, scripted_provider, local_task):
    await LocalIngestionPhaseHandler(session, provider=scripted_provider).ingest(run_id=local_task.run_id)
    assert await count_inputs(session, local_task.run_id) == 2
    assert "normalize-local-organization-batch" in scripted_provider.requests[0].messages[0].content
    assert await event_exists(session, local_task.run_id, "ingestion_page_persisted")

async def test_unrecognizable_source_generates_abnormal_report(session, scripted_provider, local_task):
    scripted_provider.output = {"result": {"recognized": False, "safe_problem_codes": ["unknown_schema"]}}
    result = await factory.ingest(work_context(local_task))
    assert result.next_phase == AgentPhase.GENERATE_REPORT
    assert await report_state(session, local_task.task_id) == "abnormal_input"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd backend && .venv/bin/pytest tests/integration/agent_runtime/test_agent_ingestion_handler.py tests/integration/agent_runtime/test_csv_analysis_worker.py -q`

Expected: FAIL because ingestion is currently a CSV adapter and emits one final event.

- [ ] **Step 3: Implement paged ingestion with server-side membership validation**

```python
for source_role, source_version in task.local_source_versions():
    inspection = await self._ingestion_agent.inspect(source_version)
    if not inspection.recognized:
        raise AgentContractError("unrecognizable_input_schema")
    async for page in self._sources.pages(source_version, limit=50):
        normalized = await self._ingestion_agent.normalize(source_role, page)
        validate_normalized_page(normalized, page)
        await self._analysis.persist_inputs(to_contract_records(normalized, page))
        await self._analysis.persist_marks(to_marks(normalized, page))
        await self._runtime.append_event(run.id, "ingestion_page_persisted", safe_counts(page, normalized))
```

The model never chooses files, source roles, page size, row locators, phone values or persistence identifiers. It only returns a validated mapping/classification for the supplied page.

- [ ] **Step 4: Run focused verification**

Run: `cd backend && .venv/bin/pytest tests/integration/agent_runtime/test_agent_ingestion_handler.py tests/integration/agent_runtime/test_csv_analysis_worker.py -q && .venv/bin/ruff check app/agent_runtime app/ai && .venv/bin/mypy app/agent_runtime app/ai`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_runtime backend/app/ai backend/tests/integration/agent_runtime
git commit -m "feat: run local ingestion through agent skills"
```

## Task 5: Bind analysis, governance, report and rollback phases to complete Skills

**Files:**

- Modify: `backend/app/ai/agent_analysis_service.py`
- Modify: `backend/app/agent_runtime/csv_governance_handlers.py`
- Modify: `backend/app/agent_runtime/csv_rollback_handlers.py`
- Modify: `backend/app/agent_reporting/service.py`
- Test: `backend/tests/unit/ai/test_agent_analysis_service.py`
- Test: `backend/tests/unit/governance/test_agent_governance.py`
- Test: `backend/tests/integration/agent_reporting/test_agent_reporting_and_rollback.py`

**Interfaces:**

- `build_agent_request(skill, payload, output_model)` is the only model-request path for every phase Agent.
- Events/checkpoints record Skill name/version and safe model metadata.
- The server continues to compile operations, require approvals, verify outcomes and decide rollback eligibility.

- [ ] **Step 1: Write failing Skill-binding tests**

```python
async def test_analysis_request_contains_complete_skills(service, provider):
    await service.analyze(tenant_id="school-1", task_id=uuid4(), work_items=(work_item(),))
    system = provider.requests[0].messages[0].content
    assert "untrusted evidence" in system
    assert "reconcile-entity-batch" in system
    assert "generate-governance-solutions" in system

async def test_report_cannot_be_rollback_eligible_without_verified_execution(session):
    report = await AgentReportingService(session).generate(task_id=uuid4(), tenant_id="school-1",
                                                           kind="sync", terminal_state="completed",
                                                           facts={"mutations": []})
    assert report.rollback_eligible is False
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd backend && .venv/bin/pytest tests/unit/ai/test_agent_analysis_service.py tests/unit/governance/test_agent_governance.py tests/integration/agent_reporting/test_agent_reporting_and_rollback.py -q`

Expected: FAIL until the common prompt builder and versioned Skill metadata are used everywhere.

- [ ] **Step 3: Route every model phase through the common builder**

```python
request = build_agent_request(
    skill=self._skills.load("generate-agent-governance-report", "1.0.0"),
    input_payload=report_input.model_dump(mode="json"),
    output_model=AgentGovernanceReport,
)
output = await self._provider.complete_json_once(request)
report = AgentGovernanceReport.model_validate(output.output["result"])
```

Apply this pattern to reconciliation/solution, approval aggregation, execution outcome, report narrative, rollback assessment and rollback execution. The executor ignores model-generated operations and executes only server-compiled, approved, version-valid rows.

- [ ] **Step 4: Run phase verification**

Run: `cd backend && .venv/bin/pytest tests/unit/ai/test_agent_analysis_service.py tests/unit/governance/test_agent_governance.py tests/integration/agent_reporting/test_agent_reporting_and_rollback.py -q && .venv/bin/ruff check app/ai app/agent_runtime app/agent_reporting && .venv/bin/mypy app/ai app/agent_runtime app/agent_reporting`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai backend/app/agent_runtime backend/app/agent_reporting backend/tests/unit/ai backend/tests/unit/governance backend/tests/integration/agent_reporting
git commit -m "feat: bind durable agent phases to skills"
```

## Task 6: Frontend conversation and manual-sync workbench integration

**Files:**

- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`
- Modify: `frontend/src/features/task-create/TaskCreatePage.tsx`
- Modify: `frontend/src/features/task-create/TaskCreatePage.test.tsx`
- Modify: `frontend/src/app/WorkspaceSidebar.tsx`
- Modify: the existing stylesheet owning task-create classes.

**Interfaces:**

- Conversation API exposes supervisor decision kinds, safe source summaries and server-owned start confirmation.
- History is invalidated after confirmation and includes unfinished runs.
- The Apple-style workbench displays Chinese progress summaries rather than raw event identifiers.

- [ ] **Step 1: Write failing interaction tests**

```tsx
it("starts a confirmed conversation task and makes it recoverable from history", async () => {
  render(<ConversationCreatePage agentApi={fakeAgentApi} />);
  await user.type(screen.getByLabelText("对账目标"), "同步七年级学生");
  await user.click(screen.getByRole("button", { name: "发送" }));
  await user.click(await screen.findByRole("button", { name: "确认开始同步" }));
  expect(await screen.findByText("任务已开始")).toBeInTheDocument();
  expect(fakeAgentApi.startTask).toHaveBeenCalledTimes(1);
});

it("shows Chinese ingestion progress rather than internal event identifiers", async () => {
  render(<ConversationCreatePage agentApi={agentWithEvents("ingestion_page_persisted")} />);
  expect(await screen.findByText(/数据接入/)).toBeInTheDocument();
  expect(screen.queryByText("ingestion_page_persisted")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd frontend && npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx src/features/task-create/TaskCreatePage.test.tsx`

Expected: FAIL until the new API contracts and query invalidation are implemented.

- [ ] **Step 3: Implement typed response mapping and the minimal workbench**

```tsx
const response = await backendApi.sendMessage(conversationId, message);
setMessages((items) => [...items, toAssistantMessage(response)]);
setConfirmation(response.start_confirmation);

await backendApi.startTask(conversationId, confirmation.intent, sessionKey());
await queryClient.invalidateQueries({ queryKey: ["agent-history"] });
```

Render only safe Chinese messages, source summary, selected entities, counts, state badge and one progress timeline. Disable normal input after task start except when the server emits an identity clarification. Preserve deletion only when the backend sets `deletion_eligible`.

- [ ] **Step 4: Run frontend verification**

Run: `cd frontend && npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx src/features/task-create/TaskCreatePage.test.tsx && npm run lint && npm run typecheck && npm run build`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat: connect workbench to agent tasks"
```

## Task 7: End-to-end verification, documentation and cleanup

**Files:**

- Modify: the existing environment template and startup documentation.
- Modify: `openspec/changes/new-agent-architecture/tasks.md`.
- Test: existing backend and frontend suites.

- [ ] **Step 1: Add a configuration smoke test**

```python
def test_demo_settings_canonicalize_a_safe_local_root(tmp_path: Path) -> None:
    settings = Settings(agent_local_read_roots=(tmp_path,))
    assert settings.agent_local_read_roots == (tmp_path.resolve(),)
```

- [ ] **Step 2: Run focused end-to-end smoke tests**

Run: `cd backend && .venv/bin/pytest tests/unit/local_sources tests/unit/ai/test_conversation_agent.py tests/integration/api/test_agent_api.py tests/integration/agent_runtime/test_agent_ingestion_handler.py -q`

Expected: PASS.

- [ ] **Step 3: Document the real local workflow and remove superseded code**

Document: configure approved local root, start PostgreSQL, migrate, run FastAPI, run `python -m app.agent_runtime`, then start the frontend. Delete `_merge_conversation_intent`, browser-upload-only conversation branches and unused CSS/test helpers only after their replacements are covered.

- [ ] **Step 4: Run the full gates**

Run: `cd backend && .venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/mypy app`

Expected: PASS, with only the documented migration smoke skip when its dedicated database URL is absent.

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck && npm run build`

Expected: PASS.

- [ ] **Step 5: Run clean PostgreSQL migration smoke**

Run: `cd backend && RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q`

Expected: PASS with Docker PostgreSQL running.

- [ ] **Step 6: Commit documentation and cleanup**

```bash
git add backend frontend docs openspec
git commit -m "docs: document local agent synchronization"
```

## Plan self-review

- Spec coverage: Tasks 1-4 cover configured local discovery, complete supervisor and ingestion behavior, durable creation/history and observable progress. Task 5 binds all remaining phase agents to versioned prompts while preserving safety checks. Task 6 covers both user-facing entry points. Task 7 validates migration, configuration and removal of superseded paths.
- Placeholder scan: the plan contains no `TODO`, `TBD`, unspecified error handling or deferred implementation steps.
- Type consistency: `ConversationAgentDecision`, `ConversationSupervisorAgent.reply`, `LocalSourceService.read_page`, `LocalIngestionPhaseHandler.ingest` and `build_agent_request` are defined before later tasks consume them.


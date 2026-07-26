# Codex Workbench And Conversation Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a consistent Codex-style light workbench, complete persisted LLM conversation context, and an atomic “new conversation” action that deletes chat without deleting governance facts.

**Architecture:** PostgreSQL remains the source of truth for conversation messages. The conversation route appends the current user message, reloads the complete ordered history, passes that history through a bounded conversation context contract, and blocks before provider invocation when the configured context budget is exceeded. A transactional repository operation replaces all inactive operator conversations with one new conversation while active school tasks remain guarded. The React application uses the existing workspace shell, a shared light token layer, and a reusable collapsible task-status rail.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy async, Alembic, pytest, React 18, TypeScript, TanStack Query, Ant Design, Vitest, Testing Library, Playwright, CSS.

## Global Constraints

- Do not change Agent state graph guards, risk classification, approvals, governance execution, reporting facts, rollback facts, or local CSV writeback.
- The trusted tenant is always `OperatorContext.tenant_id`; the client cannot submit or override it.
- The model receives the complete current conversation. Do not summarize or silently truncate older messages.
- Stop before model invocation when the complete request exceeds the configured budget.
- Reset permanently deletes chat records only; task, run, approval, report, audit, and rollback records remain.
- Reject reset while any school-wide sync or rollback task is active.
- Preserve existing function and button locations except for the approved header reset action and right task-status rail.
- User-visible final stage copy is `报告生成`; internal phase and business-stage identifiers remain unchanged.
- Use TDD for every behavior change.
- Do not commit `.env`, generated CSV exports, brainstorm files, credentials, or real organization data.

---

### Task 1: Conversation history and context budget contract

**Files:**
- Modify: `backend/app/schemas/agent_conversation.py`
- Create: `backend/app/ai/conversation_context.py`
- Modify: `backend/app/ai/conversation_agent.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/ai/skills/converse-school-data-sync/SKILL.md`
- Modify: `backend/.env.example`
- Modify: `backend/README.md`
- Test: `backend/tests/unit/ai/test_conversation_agent.py`
- Test: `backend/tests/unit/core/test_config.py`
- Test: `backend/tests/unit/ai/test_agent_skill_content.py`

**Interfaces:**
- Produces: `ConversationHistoryMessage(role, kind, text)`.
- Produces: `ConversationContextLimitError(estimated_tokens, available_tokens)`.
- Produces: `ensure_conversation_request_fits(request, max_context_tokens, reserved_output_tokens)`.
- Changes: `ConversationSupervisorAgent(provider, *, max_context_tokens, reserved_output_tokens)`.
- Consumes later: the API route passes complete ordered messages through `ConversationAgentContext.history`.

- [ ] **Step 1: Write failing complete-history request tests**

Add a unit test that creates this context:

```python
history = (
    ConversationHistoryMessage(role="user", kind="normal", text="我要同步学生"),
    ConversationHistoryMessage(
        role="assistant",
        kind="normal",
        text="请选择第三方和希沃数据来源",
    ),
    ConversationHistoryMessage(role="user", kind="normal", text="继续"),
)
```

Call `ConversationSupervisorAgent(provider, max_context_tokens=16_384, reserved_output_tokens=2_048).reply(_context(history=history))` and assert the provider request evidence contains all three messages in order with `role`, `kind`, and `text`.

- [ ] **Step 2: Run the history test and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/ai/test_conversation_agent.py::test_supervisor_sends_complete_ordered_history -q
```

Expected: FAIL because `ConversationHistoryMessage` and `history` do not exist.

- [ ] **Step 3: Write failing context-limit tests**

Add tests asserting:

```python
with pytest.raises(ConversationContextLimitError) as captured:
    ensure_conversation_request_fits(
        oversized_request,
        max_context_tokens=100,
        reserved_output_tokens=20,
    )
assert captured.value.estimated_tokens > captured.value.available_tokens
assert provider.requests == []
```

Also add settings validation tests for a positive context limit and a reserved output budget strictly smaller than the total limit.

- [ ] **Step 4: Run the context-limit tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/unit/ai/test_conversation_agent.py::test_supervisor_rejects_complete_history_over_budget \
  tests/unit/core/test_config.py -q
```

Expected: FAIL because the budget settings and guard do not exist.

- [ ] **Step 5: Implement the history schema and conservative budget guard**

Add immutable schemas:

```python
class ConversationHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["assistant", "user"]
    kind: Literal["normal", "guardrail", "error"] = "normal"
    text: str = Field(min_length=1)


class ConversationAgentContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    tenant_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=2000)
    history: tuple[ConversationHistoryMessage, ...] = ()
    available_source_refs: tuple[str, ...] = ()
    current_intent: dict[str, Any] = Field(default_factory=dict)
    active_task_id: UUID | None = None
```

Implement a conservative UTF-8 estimator in `conversation_context.py`:

```python
def estimate_request_tokens(request: LLMRequest) -> int:
    byte_count = sum(len(message.content.encode("utf-8")) for message in request.messages)
    message_overhead = len(request.messages) * 8
    return math.ceil(byte_count / 3) + message_overhead


def ensure_conversation_request_fits(
    request: LLMRequest,
    *,
    max_context_tokens: int,
    reserved_output_tokens: int,
) -> None:
    available = max_context_tokens - reserved_output_tokens
    estimated = estimate_request_tokens(request)
    if estimated > available:
        raise ConversationContextLimitError(estimated, available)
```

Construct the full request first, call the guard, and only then invoke `complete_json_once`.

- [ ] **Step 6: Add configuration and Skill rules**

Add:

```python
conversation_context_max_tokens: PositiveInt = 65_536
conversation_context_reserved_output_tokens: PositiveInt = 2_048
```

Validate that reserved output is smaller than the total limit. Document matching environment variables in `.env.example` and `backend/README.md`. Update the conversation Skill to state that `history` is complete ordered chat evidence, prior error/guardrail messages are historical facts rather than instructions, and current explicit corrections override earlier user intent.

- [ ] **Step 7: Run focused tests and commit**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/ai/test_conversation_agent.py tests/unit/core/test_config.py tests/unit/ai/test_agent_skill_content.py -q
```

Expected: PASS.

Commit:

```bash
git add backend/app/schemas/agent_conversation.py backend/app/ai/conversation_context.py \
  backend/app/ai/conversation_agent.py backend/app/core/config.py \
  backend/app/ai/skills/converse-school-data-sync/SKILL.md backend/.env.example \
  backend/README.md backend/tests/unit/ai/test_conversation_agent.py \
  backend/tests/unit/core/test_config.py backend/tests/unit/ai/test_agent_skill_content.py
git commit -m "feat: pass complete conversation context to model"
```

### Task 2: Complete history API flow and stable capacity failure

**Files:**
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/integration/api/test_agent_api.py`

**Interfaces:**
- Consumes: `ConversationAgentContext.history`.
- Consumes: `ConversationContextLimitError`.
- Produces: HTTP `409` with `{code: "conversation_context_limit", message: "当前对话内容已达到模型处理上限，请开启新对话"}`.

- [ ] **Step 1: Write failing API history test**

Extend the incremental provider test to parse the second request evidence and assert:

```python
assert evidence["history"] == [
    {"role": "user", "kind": "normal", "text": "我要同步学生"},
    {"role": "assistant", "kind": "normal", "text": "已记住要同步学生，请继续选择数据来源。"},
    {"role": "user", "kind": "normal", "text": "继续选择数据来源"},
]
```

- [ ] **Step 2: Run the API history test and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_agent_api.py::test_conversation_model_receives_complete_persisted_history -q
```

Expected: FAIL because the route still sends only the current message and private intent.

- [ ] **Step 3: Write failing API capacity test**

Configure a tiny context budget, send a message, and assert:

```python
assert response.status_code == 409
assert response.json()["detail"]["code"] == "conversation_context_limit"
assert provider.requests == []
assert current.json()["messages"][0]["role"] == "user"
```

- [ ] **Step 4: Run the capacity test and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_agent_api.py::test_conversation_context_limit_preserves_user_message_without_calling_model -q
```

Expected: FAIL because no capacity error is mapped.

- [ ] **Step 5: Load persisted history and map the stable error**

After appending the user message, reload messages with `list_conversation_messages`, map each record to `ConversationHistoryMessage`, and instantiate the supervisor with the settings budgets. Catch `ConversationContextLimitError` before the existing model-provider catch, append no synthetic assistant reply, commit the user message, and raise the stable `409`.

- [ ] **Step 6: Run API conversation tests and commit**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_agent_api.py -q
```

Expected: PASS.

Commit:

```bash
git add backend/app/api/routes/agent.py backend/tests/integration/api/test_agent_api.py
git commit -m "feat: enforce complete conversation capacity"
```

### Task 3: Atomic new-conversation reset

**Files:**
- Modify: `backend/app/models/agent_runtime.py`
- Modify: `backend/app/agent_runtime/repository.py`
- Modify: `backend/app/schemas/agent_api.py`
- Modify: `backend/app/api/routes/agent.py`
- Create: `backend/alembic/versions/0033_conversation_reset.py`
- Test: `backend/tests/integration/api/test_agent_api.py`
- Test: `backend/tests/unit/models/test_agent_runtime_models.py`
- Test: `backend/tests/integration/test_migrations.py`

**Interfaces:**
- Produces: `POST /api/agent/conversations/current/reset`.
- Requires: `Idempotency-Key` header, 1–128 characters.
- Produces: a new `AgentConversationResponse`.
- Produces: `ConversationResetConflict(owner_task_id)` for an active school lock.
- Persists: nullable `reset_idempotency_key` with uniqueness scoped by tenant and operator.

- [ ] **Step 1: Write failing reset behavior tests**

Add integration tests that:

1. Create two inactive conversations with messages.
2. Seed a completed run, report, and task referencing one conversation.
3. Reset with a fixed idempotency key.
4. Assert both old conversations and messages are deleted.
5. Assert exactly one new active conversation exists.
6. Assert task/run/report facts still exist and the run conversation reference is null.
7. Repeat the same reset and assert the same new conversation ID is returned.

- [ ] **Step 2: Run reset tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_agent_api.py -k conversation_reset -q
```

Expected: FAIL with route not found.

- [ ] **Step 3: Write failing active-lock and tenant tests**

Start a normal sync task, call reset, and assert:

```python
assert response.status_code == 409
assert response.json()["detail"]["code"] == "conversation_active_task"
assert response.json()["detail"]["owner_task_id"] == task_id
```

Also assert another tenant cannot use the idempotency key to retrieve or delete the first tenant’s conversation.

- [ ] **Step 4: Add reset persistence and migration**

Add to `AgentConversationRecord`:

```python
reset_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
```

Add unique constraints for `(tenant_id, created_by, reset_idempotency_key)` and one active conversation per `(tenant_id, created_by)`. Migration `0033` must:

- rank existing active conversations by active-run ownership, then creation time;
- delete inactive duplicates while preserving task/run/report facts through existing `SET NULL`;
- add the reset key column and indexes.

- [ ] **Step 5: Implement transactional repository reset**

Implement:

```python
async def reset_conversation(
    self,
    *,
    tenant_id: str,
    created_by: str,
    idempotency_key: str,
) -> AgentConversationRecord:
```

The method first returns an existing row with the same scoped reset key. Otherwise it locks the operator’s conversation rows, rejects an active `SchoolTaskLockRecord`, deletes all inactive conversations for the operator, flushes cascades, creates one active conversation carrying the reset key, and returns it. `create_conversation` must reuse the existing current conversation rather than accumulating new empty conversations.

- [ ] **Step 6: Expose reset endpoint and stable errors**

Add the `POST /conversations/current/reset` route. Read the trusted operator from dependencies, pass the header idempotency key, map an active lock to `conversation_active_task`, and never accept tenant in the body.

- [ ] **Step 7: Run migration and API tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/models/test_agent_runtime_models.py -q
.venv/bin/pytest tests/integration/api/test_agent_api.py -q
```

If Docker is available, also run:

```bash
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/agent_runtime.py backend/app/agent_runtime/repository.py \
  backend/app/schemas/agent_api.py backend/app/api/routes/agent.py \
  backend/alembic/versions/0033_conversation_reset.py \
  backend/tests/integration/api/test_agent_api.py \
  backend/tests/unit/models/test_agent_runtime_models.py
git commit -m "feat: atomically reset agent conversations"
```

### Task 4: Frontend complete-context failure and new-conversation action

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/client.test.ts`
- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/api/agent.test.ts`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

**Interfaces:**
- Produces: `ApiError.code?: string`.
- Produces: `AgentConversationApi.resetConversation(idempotencyKey)`.
- Consumes: `conversation_active_task` and `conversation_context_limit`.

- [ ] **Step 1: Write failing API client tests**

Assert `requestJson` preserves both server message and error code:

```typescript
await expect(requestJson("/api/agent/conversations/current/reset")).rejects.toMatchObject({
  status: 409,
  code: "conversation_active_task",
  message: "当前学校仍有任务正在处理",
});
```

Assert `resetConversation("reset-1")` sends `POST`, an empty JSON body, and the `Idempotency-Key` header.

- [ ] **Step 2: Run API tests and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/api/client.test.ts src/api/agent.test.ts
```

Expected: FAIL because the code and reset method are absent.

- [ ] **Step 3: Implement typed API errors and reset method**

Extend `ApiError`:

```typescript
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}
```

Parse `detail.code`, add `resetConversation`, add it to `AgentConversationApi`, and export it from `agentApi`.

- [ ] **Step 4: Write failing conversation UI tests**

Tests must cover:

- the header displays `开启新对话`;
- active tasks disable the action and expose an explanatory title;
- confirmation text says chat is permanent but governance facts remain;
- confirm calls reset once and replaces old messages with the empty new conversation;
- reset failure preserves existing messages;
- `conversation_context_limit` shows the dedicated capacity message and emphasizes the reset action.

- [ ] **Step 5: Run page tests and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: FAIL because the reset UI does not exist.

- [ ] **Step 6: Implement the reset interaction**

Add a header action using the existing Ant Design modal pattern. Generate one client idempotency key per confirmed reset attempt. Keep the current page and messages until the API succeeds; on success set the returned ID, clear intent/confirmation/task/events, and restore only the non-persisted welcome message. Disable while hydrating, resetting, or a task is active.

- [ ] **Step 7: Run focused frontend tests and commit**

Run:

```bash
cd frontend
npm test -- --run src/api/client.test.ts src/api/agent.test.ts \
  src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: PASS.

Commit:

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts \
  frontend/src/api/agent.ts frontend/src/api/agent.test.ts \
  frontend/src/features/task-create/ConversationCreatePage.tsx \
  frontend/src/features/task-create/ConversationCreatePage.test.tsx
git commit -m "feat: add permanent new conversation action"
```

### Task 5: Codex light workspace shell and collapsible task-status rail

**Files:**
- Modify: `frontend/src/styles/apple.css`
- Modify: `frontend/src/styles/global.test.ts`
- Create: `frontend/src/components/TaskStatusRail.tsx`
- Create: `frontend/src/components/TaskStatusRail.test.tsx`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.tsx`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

**Interfaces:**
- Produces: `TaskStatusRail({stages, currentIndex, blocked, terminationRequested})`.
- Persists: right rail preference under `mofa-task-status-collapsed`.
- Leaves: left rail preference under `mofa-workspace-collapsed`.

- [ ] **Step 1: Write failing theme-token tests**

Replace dark-theme assertions with exact light tokens:

```typescript
expect(appleCss).toMatch(/--codex-canvas:\s*#ffffff/);
expect(appleCss).toMatch(/--codex-sidebar:\s*#f6f7f8/);
expect(appleCss).toMatch(/--codex-ink:\s*#202123/);
expect(appleCss).not.toMatch(/radial-gradient/);
expect(appleCss).not.toMatch(/apple-drift/);
```

Also require dedicated, readable selectors for user messages, assistant messages, process cards, reports, approvals, and modal surfaces.

- [ ] **Step 2: Run the style test and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/styles/global.test.ts
```

Expected: FAIL because the current Apple stylesheet is a dark gradient theme.

- [ ] **Step 3: Write failing rail tests**

Render `TaskStatusRail`, click `收起任务处理状态`, and assert:

```typescript
expect(localStorage.getItem("mofa-task-status-collapsed")).toBe("true");
expect(screen.getByRole("button", { name: "展开任务处理状态" })).toBeVisible();
```

Rerender and assert the preference is restored. Assert current, completed, waiting, blocked, and termination-report labels remain visible to assistive technology.

- [ ] **Step 4: Run rail tests and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/components/TaskStatusRail.test.tsx
```

Expected: FAIL because the component does not exist.

- [ ] **Step 5: Rebuild the light token layer**

Replace the dark Apple overrides with a flat token system:

```css
:root {
  --codex-canvas: #ffffff;
  --codex-sidebar: #f6f7f8;
  --codex-panel: #ffffff;
  --codex-panel-subtle: #f7f8fa;
  --codex-ink: #202123;
  --codex-ink-soft: #5f6368;
  --codex-ink-faint: #8b9098;
  --codex-border: #e3e5e8;
  --codex-accent: #315efb;
  --codex-user: #eaf1ff;
  --codex-assistant: #f4f5f7;
  --codex-process: #eef2f7;
  --codex-danger: #b42318;
  --codex-shadow: 0 8px 24px rgb(15 23 42 / 7%);
}
```

Use white main canvas, gray-white navigation, thin borders, restrained shadows, no gradients, no decorative animated blobs, and visible focus rings. Keep existing class names so business components do not require rewrites.

- [ ] **Step 6: Implement and integrate the task-status rail**

Create a focused component with a semantic `<aside>`, persisted collapse state, stage list, and mobile behavior. Integrate it into task detail and active conversation layouts. Move only the visual stage representation into the rail; do not move approval, termination, report, or task actions.

- [ ] **Step 7: Rename final user-facing stage**

Change user-visible phase labels from `报告与回滚` to `报告生成` in task detail and presentation tests. Keep `generate_report` and `report_and_rollback` internal IDs unchanged.

- [ ] **Step 8: Run focused UI tests and commit**

Run:

```bash
cd frontend
npm test -- --run src/styles/global.test.ts \
  src/components/TaskStatusRail.test.tsx \
  src/features/task-detail/AgentTaskDetailPage.test.tsx \
  src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: PASS.

Commit:

```bash
git add frontend/src/styles/apple.css frontend/src/styles/global.test.ts \
  frontend/src/components/TaskStatusRail.tsx frontend/src/components/TaskStatusRail.test.tsx \
  frontend/src/features/task-detail/AgentTaskDetailPage.tsx \
  frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx \
  frontend/src/features/task-create/ConversationCreatePage.tsx \
  frontend/src/features/task-create/ConversationCreatePage.test.tsx
git commit -m "feat: apply codex light workbench shell"
```

### Task 6: Migrate remaining pages and report reading surface

**Files:**
- Modify: `frontend/src/features/reports/AgentReportPage.tsx`
- Modify: `frontend/src/features/reports/AgentReportPage.test.tsx`
- Modify: `frontend/src/features/differences/DifferenceCategoryPage.tsx`
- Modify: `frontend/src/features/differences/DifferenceCategoryPage.test.tsx`
- Modify: `frontend/src/features/executions/ExecutionHistoryPage.tsx`
- Modify: `frontend/src/features/executions/ExecutionDetailPage.tsx`
- Modify: `frontend/src/features/task-detail/TaskDetailPage.tsx`
- Modify: `frontend/src/styles/apple.css`
- Modify: `frontend/src/app/App.test.tsx`

**Interfaces:**
- Consumes: shared `apple-page`/Codex token classes.
- Preserves: all current routes, controls, risk review semantics, report facts, and rollback actions.

- [ ] **Step 1: Write failing route and report style tests**

Assert every top-level route renders within the light workbench class and that the report uses:

```text
报告摘要
异常与治理建议
治理结果
```

as readable document sections. Assert no raw JSON field list replaces the narrative and no report selector uses a dark background.

- [ ] **Step 2: Run the page tests and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/features/reports/AgentReportPage.test.tsx \
  src/features/differences/DifferenceCategoryPage.test.tsx src/app/App.test.tsx
```

Expected: FAIL because legacy routes do not all opt into the shared workbench surface.

- [ ] **Step 3: Apply shared page classes without changing behavior**

Add `apple-page` to legacy task, difference, execution-history, and execution-detail main surfaces. Add scoped Codex selectors for tables, empty states, skeletons, alerts, forms, approval panels, and operation history. Preserve all click handlers and API calls.

- [ ] **Step 4: Refine the report as a document**

Keep the LLM narrative and persisted facts, but present them in a restrained document layout with a compact summary header, readable paragraphs, findings, suggested resolutions, excluded invalid rows, and actual mutation results. Avoid oversized metric cards and dark panels.

- [ ] **Step 5: Run frontend suite and commit**

Run:

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: PASS.

Commit:

```bash
git add frontend/src
git commit -m "feat: unify reconciliation pages with codex theme"
```

### Task 7: Full verification and browser regression

**Files:**
- Modify only if verification exposes an in-scope defect.

**Interfaces:**
- Verifies all prior deliverables together.

- [ ] **Step 1: Run backend quality gates**

Run:

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

Expected: PASS, excluding only the documented migration smoke test when its environment variable is absent.

- [ ] **Step 2: Run clean PostgreSQL migration smoke test**

With Docker/PostgreSQL available:

```bash
cd backend
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

Expected: PASS.

- [ ] **Step 3: Run frontend quality gates**

Run:

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: PASS.

- [ ] **Step 4: Run browser regression**

Start the complete development stack, then verify:

- left navigation collapse persists;
- right task-status rail collapse persists;
- new-conversation confirmation and success state work;
- active task disables reset;
- complete chat remains after route changes and refresh;
- stage copy says `报告生成`;
- conversation, external sync, task detail, approval, report, history, empty, and error screens use the same light palette;
- no user or assistant text has white-on-white contrast;
- mobile layout keeps the main task controls reachable.

- [ ] **Step 5: Inspect the final diff**

Run:

```bash
git status --short
git diff --check
git diff --stat master...HEAD
```

Expected: only planned source, test, migration, and documentation changes; no `.env`, generated CSV, storage export, dependency directory, or brainstorm artifact.

- [ ] **Step 6: Final verification commit if needed**

If verification required an in-scope correction, commit only those files:

```bash
git add backend/app backend/tests backend/alembic/versions/0033_conversation_reset.py frontend/src
git commit -m "fix: complete conversation workbench verification"
```

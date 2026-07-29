# Identity conflict clarification reliability implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make explicit identity-conflict selections deterministic, immediately read-only, usable from both task detail and new conversation, and prevent valid cross-category correspondences from failing risk aggregation.

**Architecture:** Add a bounded structured-selection endpoint beside the existing model-backed clarification endpoint, persist the active selection on `AgentClarificationRecord`, and expose it through the graph view. A shared React card submits the structured decision and retains an optimistic read-only snapshot until polling reconciles it. The existing confirmation endpoint remains the only way to resume a waiting graph run.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic v2, pytest, React 18, TypeScript, TanStack Query, Ant Design, Vitest/Testing Library, Playwright.

## Global constraints

- [ ] Keep the graph sequence unchanged:
  `resolve_identity_conflicts -> analyze_actionable_batches -> aggregate_risk -> wait_high_risk_approvals`.
- [ ] Do not edit graph versions, node definitions, successor selection, action ordering, worker routing, or `_resume_after_clarifications`.
- [ ] Keep the legacy free-text clarification endpoint available.
- [ ] Keep the existing second-confirmation endpoint and safety boundary.
- [ ] Use only synthetic identity data in tests.
- [ ] Run each focused test once before implementation and observe the intended failure.

---

## Task 1: Define and persist structured identity selections

**Files:**

- Modify: `backend/app/schemas/agent_api.py`
- Modify: `backend/app/repositories/agent_governance.py`
- Test: `backend/tests/unit/repositories/test_agent_governance_repository.py`

- [ ] **Step 1: Add failing repository tests**

Cover these observable rules:

- a pending record accepts a candidate selection and becomes `interpreted`;
- the stored interpretation contains only the bounded decision, frozen candidate ID, optional note,
  canonical Chinese summary, `submission_source`, and idempotency key;
- replaying the same key and payload returns the existing record without creating a new write;
- a new key replaces an `interpreted` selection;
- a confirmed record rejects replacement.

Use an API-facing request schema with this contract:

```python
class StructuredClarificationSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["select_candidate", "treat_as_extra"]
    selected_candidate_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)
    graph_cursor: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)
```

Add an `after` validator that trims `note` and requires a candidate only for
`select_candidate`.

- [ ] **Step 2: Run the repository test and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/repositories/test_agent_governance_repository.py -q
```

Expected: failure because the structured persistence method and schema do not exist.

- [ ] **Step 3: Implement the minimal repository method**

Add a method such as:

```python
async def record_structured_clarification_selection(
    self,
    *,
    clarification_id: UUID,
    tenant_id: UUID,
    decision: Literal["select_candidate", "treat_as_extra"],
    selected_candidate_id: UUID | None,
    note: str | None,
    interpretation_zh: str,
    idempotency_key: str,
    actor_id: UUID,
) -> tuple[AgentClarificationRecord, bool]:
    return record, created_or_replaced
```

The returned boolean is `True` only when this call persisted a new/replacement selection. Lock the
record, scope it to the tenant, compare the idempotency key before writing, and reject confirmed
records. Store a safe canonical sentence in `original_text` when the note is empty.

- [ ] **Step 4: Run the repository test and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/repositories/test_agent_governance_repository.py -q
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/agent_api.py backend/app/repositories/agent_governance.py backend/tests/unit/repositories/test_agent_governance_repository.py
git commit -m "feat: persist structured identity selections"
```

---

## Task 2: Add the structured-selection endpoint and graph response

**Files:**

- Modify: `backend/app/schemas/agent_graph_api.py`
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/integration/api/test_agent_graph_api.py`

- [ ] **Step 1: Add failing integration tests**

Seed one `agent-graph-v1` task waiting at `resolve_identity_conflicts`, with a pending identity gate
and two frozen candidates. Test:

1. selecting candidate A returns `interpreted` and requires second confirmation;
2. the model provider spy has no calls;
3. the graph view exposes the persisted `operator_submission`;
4. replaying the same idempotency key does not append another
   `clarification_decision_ready` event;
5. a new key replaces the active interpreted selection;
6. an unknown candidate, disallowed target-extra outcome, stale graph cursor, wrong tenant, or
   non-actionable gate is rejected;
7. confirmation still uses the existing endpoint.

The graph addition is:

```python
class AgentGraphClarificationSubmissionView(BaseModel):
    decision: Literal["select_candidate", "treat_as_extra"]
    selected_candidate_id: UUID | None = None
    note: str | None = None
    interpretation_zh: str
    submitted_at: datetime
    source: Literal["structured_selection"]
```

and `AgentGraphIdentityConflictView` gains:

```python
operator_submission: AgentGraphClarificationSubmissionView | None = None
```

- [ ] **Step 2: Run the focused integration tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_agent_graph_api.py -k "structured_identity" -q
```

Expected: 404 or import/attribute failure because the endpoint and view do not exist.

- [ ] **Step 3: Implement the endpoint**

Add:

```text
POST /api/agent/tasks/{task_id}/clarifications/{clarification_id}/selection
```

Validate the authenticated task/run/clarification/gate relationship, graph version, current node,
pending gate membership, exact cursor, complete frozen evidence, allowed outcome, and candidate
membership before writing. Build the Chinese summary from the server-owned candidate order; never
accept an operator-supplied label or evidence.

Call the repository method from Task 1 and append `clarification_decision_ready` only when its
`created_or_replaced` flag is true. Do not call a model provider. Do not invoke the graph runtime or
resume helper.

- [ ] **Step 4: Serialize the submission into graph reads**

When `interpretation.submission_source == "structured_selection"`, map the stored bounded fields to
`operator_submission`. Treat older/free-text interpretation payloads as `None`.

- [ ] **Step 5: Run focused and invariant tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_agent_graph_api.py -k "structured_identity or identity_conflict_uses_skill_model" -q
.venv/bin/pytest tests/unit/agent_graph/test_definition.py tests/integration/agent_graph/test_production_runtime.py -q
```

Expected: both new and legacy clarification paths pass, and graph definition/runtime tests remain
unchanged.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/agent_graph_api.py backend/app/api/routes/agent.py backend/tests/integration/api/test_agent_graph_api.py
git commit -m "feat: add structured identity clarification API"
```

---

## Task 3: Build the shared read-only clarification card

**Files:**

- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/components/IdentityConflictEvidence.tsx`
- Create: `frontend/src/components/IdentityConflictClarificationCard.tsx`
- Create: `frontend/src/components/IdentityConflictClarificationCard.test.tsx`
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/styles/apple.css`

- [ ] **Step 1: Add failing component tests**

Use a deferred promise for `submitClarificationSelection` and assert:

- frozen candidates and the allowed target-extra choice render as radios;
- a selection is required and the note is optional/max 500;
- clicking `提交选择` immediately removes the submit button;
- the selected summary and note remain visible but cannot be edited while the promise is pending;
- a successful response changes `正在保存` to `等待确认`;
- `重新选择` preserves the previous summary and opens a new blank form;
- cancelling replacement preserves the prior summary;
- failed submission restores the selected radio and note;
- `确认选择并继续` calls the existing confirmation method and shows the confirmed notice.

Use this minimal prop boundary:

```ts
type IdentityConflictClarificationCardProps = {
  taskId: string;
  gate: AgentGraphHumanGate;
  conflict: AgentGraphIdentityConflict;
  graphCursor: number;
  api: Pick<
    AgentConversationApi,
    "submitClarificationSelection" | "confirmClarification"
  >;
  onRefresh: () => void | Promise<void>;
  onOptimisticSubmission?: (
    clarificationId: string,
    submission: AgentClarificationSubmission | null,
  ) => void;
  onConfirmed?: (clarificationId: string) => void;
};
```

- [ ] **Step 2: Run the component test and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/components/IdentityConflictClarificationCard.test.tsx
```

- [ ] **Step 3: Add API types and method**

Extend `AgentGraphIdentityConflict` with `operator_submission`. Add:

```ts
type StructuredClarificationSelectionInput = {
  decision: "select_candidate" | "treat_as_extra";
  selected_candidate_id: string | null;
  note: string | null;
  graph_cursor: number;
  idempotency_key: string;
};
```

Implement `submitClarificationSelection(taskId, clarificationId, input)` and add it to
`AgentConversationApi`/`agentApi`.

- [ ] **Step 4: Implement the shared card**

Keep the evidence display in `IdentityConflictEvidence`. Export or reuse a single candidate-label
helper so the radio and read-only summary use the same candidate letters.

On submit, snapshot the form into an optimistic `AgentClarificationSubmission`, call
`onOptimisticSubmission`, clear editable mode immediately, then await the request. Reconcile with
the server response and refresh. On error, clear only the optimistic submission and restore the
local form values.

Generate one idempotency key per form submission with `crypto.randomUUID()` and retain it for an
in-flight retry.

- [ ] **Step 5: Run tests and typecheck**

Run:

```bash
cd frontend
npm test -- --run src/components/IdentityConflictClarificationCard.test.tsx
npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/agent.ts frontend/src/components/IdentityConflictEvidence.tsx frontend/src/components/IdentityConflictClarificationCard.tsx frontend/src/components/IdentityConflictClarificationCard.test.tsx frontend/src/styles/global.css frontend/src/styles/apple.css
git commit -m "feat: add shared identity clarification card"
```

---

## Task 4: Use the shared card on task detail

**Files:**

- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.tsx`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx`

- [ ] **Step 1: Replace/add failing page tests**

Test a pending graph conflict with two candidates. Select one and submit through the page. Assert
the old `身份冲突处理说明` textarea is absent, the new read-only summary appears immediately, and
the submit button stays absent after unmount/remount with the same `QueryClient`.

Also test that an authoritative `operator_submission` renders read-only after a fresh page load.

- [ ] **Step 2: Run the page tests and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/features/task-detail/AgentTaskDetailPage.test.tsx
```

- [ ] **Step 3: Integrate the shared card**

Remove the page-local clarification text, decision, rewrite, and interpretation state. Render one
shared card per actionable identity conflict.

Implement `onOptimisticSubmission` with:

```ts
queryClient.setQueryData<AgentGraphState>(
  ["agent-task-graph", taskId],
  (current) => updateConflictSubmission(current, clarificationId, submission),
);
```

This is the route-navigation persistence boundary. `onRefresh` invalidates/refetches task, graph,
and event queries. Keep locally confirmed IDs only to suppress stale polling until the server gate
disappears.

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd frontend
npm test -- --run src/features/task-detail/AgentTaskDetailPage.test.tsx
npm run typecheck
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/task-detail/AgentTaskDetailPage.tsx frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx
git commit -m "fix: make task clarification submissions read only"
```

---

## Task 5: Use the shared card on new conversation

**Files:**

- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

- [ ] **Step 1: Add failing conversation-page tests**

For an `agent-graph-v1` identity gate, assert:

- the same candidate radio card is visible without leaving the conversation;
- selecting, submitting, and confirming can complete the full flow;
- the ordinary conversation composer stays locked during the graph clarification;
- the legacy workflow still uses the free-text composer;
- an already persisted `operator_submission` is read-only.

- [ ] **Step 2: Run the page tests and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx
```

- [ ] **Step 3: Integrate the shared card**

Replace the graph-v1 clarification-specific local editor/interpretation/rewrite state with the
shared card. Retain only graph polling state and a local optimistic submission overlay so a stale
poll cannot reopen the form.

Restrict `sendMessage` clarification behavior to the legacy free-text workflow. Pass graph-v1
selection and confirmation calls through the shared card.

After confirmation, remove the actionable local gate and add:

```text
身份冲突选择已确认，Agent 正在继续处理。
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx
npm run typecheck
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/task-create/ConversationCreatePage.tsx frontend/src/features/task-create/ConversationCreatePage.test.tsx
git commit -m "feat: complete identity clarification in conversation"
```

---

## Task 6: Prevent cross-category aggregation failure

**Files:**

- Modify: `backend/app/agent_runtime/csv_governance_handlers.py`
- Modify: `backend/tests/unit/agent_runtime/test_csv_governance_handlers.py`

- [ ] **Step 1: Add the exact failing regression test**

Build a valid accepted correspondence where the authority record is a student with
`class_name="一班"` and the target record is a teacher with no `class_name`. Call the finding input
materializer and assert the change is produced with:

```python
before["class_name"] is None
after["class_name"] == "一班"
```

- [ ] **Step 2: Run the regression test and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/agent_runtime/test_csv_governance_handlers.py -k "cross_category" -q
```

Expected: `KeyError: 'class_name'`.

- [ ] **Step 3: Implement the minimal safe lookup**

Change only the missing-target-field fallback:

```python
return {
    key: raw_values.get(key, target_values.get(key))
    for key in fields
}
```

Do not change category matching, finding generation, graph execution, or risk ordering.

- [ ] **Step 4: Run focused runtime tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/agent_runtime/test_csv_governance_handlers.py -q
.venv/bin/pytest tests/unit/agent_graph/test_definition.py tests/integration/agent_graph/test_production_runtime.py -q
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_runtime/csv_governance_handlers.py backend/tests/unit/agent_runtime/test_csv_governance_handlers.py
git commit -m "fix: tolerate missing cross-category target fields"
```

---

## Task 7: Repair and extend Playwright coverage

**Files:**

- Modify: `frontend/tests/e2e/reconciliation-flow.spec.ts`
- Modify: `frontend/tests/e2e/agent-workflow.spec.ts`

- [ ] **Step 1: Fix the three baseline test defects**

Scope the task-row locator to the `历史任务` region and match the task row rather than its delete
button. Seed/mock `demo-001` in the desktop-collapse test before navigating to its detail page.

- [ ] **Step 2: Replace the legacy graph-v1 clarification E2E flow**

Mock an `agent-graph-v1` graph response with one frozen conflict and two candidates. Delay the
structured-selection response and verify:

1. select candidate A and submit;
2. the submit button disappears immediately;
3. the choice remains visible/read-only;
4. navigate to another page and back;
5. the choice is still read-only and cannot be submitted again;
6. confirm and observe the continuation notice.

Add a second assertion path through `/conversations/new` proving the same card can submit and
confirm without opening task detail.

- [ ] **Step 3: Run the focused E2E tests**

Run:

```bash
cd frontend
PLAYWRIGHT_HTML_OPEN=never npm run test:e2e -- reconciliation-flow.spec.ts agent-workflow.spec.ts
```

- [ ] **Step 4: Commit**

```bash
git add frontend/tests/e2e/reconciliation-flow.spec.ts frontend/tests/e2e/agent-workflow.spec.ts
git commit -m "test: cover durable identity clarification flow"
```

---

## Task 8: Full verification

- [ ] **Step 1: Backend quality gates**

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

- [ ] **Step 2: Clean PostgreSQL migration smoke test**

```bash
docker compose -f infra/docker-compose.yml up -d
cd backend
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

- [ ] **Step 3: Frontend quality gates**

Use the writable temporary npm cache if the global cache is still root-owned:

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
PLAYWRIGHT_HTML_OPEN=never npm run test:e2e
```

- [ ] **Step 4: Contract validation**

```bash
cd ..
openspec validate --all --strict --no-interactive
```

- [ ] **Step 5: Inspect the final diff**

```bash
git status --short
git diff --check
git log --oneline --decorate -8
```

Confirm that no graph-definition or node-order file changed and that only intended source, test,
style, and documentation files are present.

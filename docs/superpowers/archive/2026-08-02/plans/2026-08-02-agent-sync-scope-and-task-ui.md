# Agent Sync Scope and Task UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make explicit full-scope sync requests deterministic, show the conversation status rail only while a task is non-terminal, and recover cleanly from stale termination confirmations.

**Architecture:** Normalize unambiguous full-scope language at the backend model boundary before connector validation, while keeping the canonical task contract as the three existing entity types. Derive conversation layout from persisted task status, and treat termination-decision HTTP 409 responses as stale UI state that must be dismissed and refreshed.

**Tech Stack:** Python 3.12, Pydantic, pytest, React 19, TypeScript, Ant Design, React Query, Vitest, Testing Library.

## Global Constraints

- “同步全部” expands to `department`, `teacher`, and `student`; it is not a fourth entity type.
- Do not expand entity-qualified or exclusion language such as “全部学生”, “只”, “仅”, “不要”, “排除”, or “除了”.
- The conversation status rail is visible only for non-terminal persisted tasks.
- A termination-decision `409 Conflict` closes the stale modal, refreshes persisted state, and reports the server message outside the modal.
- Use synthetic test data only.

---

### Task 1: Deterministic full-scope conversation intent

**Files:**
- Modify: `backend/app/ai/conversation_agent.py`
- Modify: `backend/app/ai/skills/converse-school-data-sync/SKILL.md`
- Test: `backend/tests/unit/ai/test_conversation_agent.py`
- Test: `backend/tests/unit/ai/test_agent_skill_content.py`

**Interfaces:**
- Consumes: `ConversationAgentContext.message` and a parsed `ConversationAgentDecision`.
- Produces: `_apply_explicit_all_entity_scope(decision, context) -> ConversationAgentDecision` and Skill version `1.6.0`.

- [ ] **Step 1: Write failing normalization tests**

Add parametrized async tests whose provider returns only `student`, then assert that messages such as `同步全部` and a standalone follow-up `全部` return:

```python
assert decision.entity_types == (
    AgentEntityType.DEPARTMENT,
    AgentEntityType.TEACHER,
    AgentEntityType.STUDENT,
)
```

Add guarded cases for `同步全部学生` and `除了老师，其他全部同步` and assert the provider's `("student",)` scope remains unchanged. Update version assertions to `1.6.0` and add `全部`, `全量`, and the three canonical entity names to the Skill content contract.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest tests/unit/ai/test_conversation_agent.py tests/unit/ai/test_agent_skill_content.py -q
```

Expected: FAIL because the scope remains `student` and Skill `1.6.0` is not registered.

- [ ] **Step 3: Implement minimal backend normalization**

In `conversation_agent.py`, import `AgentEntityType`, define canonical tokens, and call the helper immediately after `_parse_decision`:

```python
_ALL_ENTITY_TYPES = (
    AgentEntityType.DEPARTMENT,
    AgentEntityType.TEACHER,
    AgentEntityType.STUDENT,
)
_FULL_SCOPE_TOKENS = ("全部", "全量", "所有")
_SYNC_ACTION_TOKENS = ("同步", "对齐", "核对", "对账")
_ENTITY_SCOPE_TOKENS = ("部门", "教师", "老师", "学生")
_SCOPE_LIMIT_TOKENS = ("只", "仅", "不要", "不含", "排除", "除了", "除外")
_STANDALONE_FULL_SCOPE_MESSAGES = {"全部", "全量", "所有", "全部都要", "全都要"}

def _apply_explicit_all_entity_scope(
    decision: ConversationAgentDecision,
    context: ConversationAgentContext,
) -> ConversationAgentDecision:
    compact = "".join(context.message.split()).strip("，。！？!?；;")
    eligible = decision.kind in {"intent_update", "start_confirmation"}
    full_scope = any(token in compact for token in _FULL_SCOPE_TOKENS)
    sync_context = (
        any(token in compact for token in _SYNC_ACTION_TOKENS)
        or compact in _STANDALONE_FULL_SCOPE_MESSAGES
    )
    constrained = any(
        token in compact
        for token in (*_ENTITY_SCOPE_TOKENS, *_SCOPE_LIMIT_TOKENS)
    )
    if not eligible or not full_scope or not sync_context or constrained:
        return decision
    return decision.model_copy(update={"entity_types": _ALL_ENTITY_TYPES})
```

Load `converse-school-data-sync` version `1.6.0`. Update its metadata and state explicitly that unqualified `全部`/`所有`/`全量` means all three entity types, while entity-qualified and exclusion language must remain constrained.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command. Expected: all selected backend unit tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/conversation_agent.py backend/app/ai/skills/converse-school-data-sync/SKILL.md backend/tests/unit/ai/test_conversation_agent.py backend/tests/unit/ai/test_agent_skill_content.py
git commit -m "fix: expand explicit full sync scope"
```

### Task 2: Conversation status rail lifecycle and full-scope copy

**Files:**
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

**Interfaces:**
- Consumes: `confirmation.entity_types` and `task.status`.
- Produces: `confirmationEntities(...)` copy and derived `showTaskStatusRail: boolean`.

- [ ] **Step 1: Write failing rendering tests**

Change the initial-page test to assert:

```typescript
expect(screen.queryByRole("complementary", { name: "任务处理状态" }))
  .not.toBeInTheDocument();
```

Add assertions that an unstarted confirmation still has no rail, a running task shows it, and restored `completed`, `terminated`, and `failed` tasks do not. For three entity types, assert `全部（部门、教师、学生）`. Assert the workspace has `has-task-status` only for the running task.

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: FAIL because the rail is unconditional and the confirmation copy lacks `全部（…）`.

- [ ] **Step 3: Implement minimal conditional rendering**

Update `confirmationEntities` to return the full-scope copy when all canonical types are present:

```typescript
if (confirmationEntityOrder.every((entityType) => selected.has(entityType))) {
  return "全部（部门、教师、学生）";
}
```

Derive and use:

```typescript
const showTaskStatusRail = Boolean(task && !terminalTaskStatuses.has(task.status));

<div className={`conversation-workspace${showTaskStatusRail ? " has-task-status" : ""}`}>
  {/* existing conversation surface */}
  {showTaskStatusRail && <TaskStatusRail {...statusRailProps} />}
</div>
```

- [ ] **Step 4: Run test and verify GREEN**

Run the Step 2 command. Expected: all `ConversationCreatePage` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/task-create/ConversationCreatePage.tsx frontend/src/features/task-create/ConversationCreatePage.test.tsx
git commit -m "fix: show task status only during active work"
```

### Task 3: Stale termination decision recovery

**Files:**
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.tsx`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`
- Test: `frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx`

**Interfaces:**
- Consumes: `ApiError.status`, termination gate ID, task/graph/event refresh APIs.
- Produces: dismissed stale gate state and refreshed task UI.

- [ ] **Step 1: Write failing 409 interaction tests**

For the conversation page, make `decideGraphGate` reject with:

```typescript
new ApiError("Gate is already decided", 409, "graph_gate_already_decided")
```

After clicking `确认终止`, assert the dialog disappears, the error is visible, and `task` plus `graph` were requested again. Add a non-409 case asserting the dialog remains.

For the task detail page, seed a persisted termination gate, reject the decision with the same `ApiError`, and assert the dialog stays closed even if the refreshed graph still contains that pending gate. Assert task, graph, and event queries refetch.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx src/features/task-detail/AgentTaskDetailPage.test.tsx
```

Expected: FAIL because both pages retain or rediscover the stale gate.

- [ ] **Step 3: Implement conversation-page conflict recovery**

In the decision catch block, detect `error instanceof ApiError && error.status === 409`, clear `terminationGate`, and call available `backendApi.task(task.id)` and `backendApi.graph(task.id)` refresh methods with `Promise.allSettled`. Apply the returned task and graph cursor when available, then keep the server error in `terminationError`. Leave non-409 behavior unchanged.

- [ ] **Step 4: Implement task-detail conflict recovery**

Import `ApiError`, add `dismissedTerminationGateId`, and exclude that ID when deriving `persistedTerminationGate`. Clear the dismissed ID when requesting a new preview. On decision 409, remember the active gate ID, clear the local gate, and run `task.refetch()`, `graph.refetch()`, and `events.refetch()` with `Promise.allSettled`. Preserve the backend error outside the modal.

- [ ] **Step 5: Run tests and verify GREEN**

Run the Step 2 command. Expected: both component test files PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/task-create/ConversationCreatePage.tsx frontend/src/features/task-create/ConversationCreatePage.test.tsx frontend/src/features/task-detail/AgentTaskDetailPage.tsx frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx
git commit -m "fix: dismiss stale termination confirmations"
```

### Task 4: Integrated verification

**Files:**
- Verify all modified files.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: evidence that the fix is ready to integrate.

- [ ] **Step 1: Run backend checks**

```bash
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest tests/unit/ai/test_conversation_agent.py tests/unit/ai/test_agent_skill_content.py -q
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/ruff check app/ai/conversation_agent.py tests/unit/ai/test_conversation_agent.py tests/unit/ai/test_agent_skill_content.py
```

Expected: PASS with no lint findings.

- [ ] **Step 2: Run frontend checks**

```bash
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx src/features/task-detail/AgentTaskDetailPage.test.tsx
npm run lint
npm run typecheck
npm run build
```

Expected: all commands exit `0`.

- [ ] **Step 3: Inspect final diff**

```bash
git diff --check HEAD~3..HEAD
git status --short
```

Expected: no whitespace errors and no uncommitted production or test changes.

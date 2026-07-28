# Conversation and sync layout fixes implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reclaim conversation space, align overview and sync-setting layouts, and prevent a completed task card from leaking into the next conversation request.

**Architecture:** Keep task history durable while separating it from the current conversation cycle. The backend omits a terminal run from the current-conversation payload when a later user message exists; the frontend immediately clears the same stale task when sending that message. Layout changes use explicit component classes and responsive CSS rather than broad element selectors.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, React, TypeScript, Vitest, Testing Library, CSS.

## Global constraints

- Preserve the left-sidebar “新建对话” navigation entry.
- Never delete or mutate the completed task when hiding its conversation card.
- Active tasks remain visible and continue locking ordinary input.
- Manual-sync controls remain keyboard accessible and collapse to one column below 720 px.
- Use synthetic data only in tests.

---

### Task 1: End the old terminal task’s conversation cycle

**Files:**
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/integration/api/test_agent_api.py`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

**Interfaces:**
- Consumes: `AgentRunRecord.updated_at` and `AgentConversationMessageRecord.created_at`.
- Produces: `GET /api/agent/conversations/current` with `task: null` after a post-terminal user message; immediate frontend removal of the old terminal task.

- [ ] **Step 1: Write backend failing regression test**

Create a completed run, append a later user conversation message, request the current conversation, and assert:

```python
assert response.status_code == 200
assert response.json()["task"] is None
```

- [ ] **Step 2: Verify backend test fails**

Run:

```bash
backend/.venv/bin/pytest backend/tests/integration/api/test_agent_api.py -q -k "hides_terminal_task_after_next_message"
```

Expected: FAIL because the endpoint still returns the completed task.

- [ ] **Step 3: Implement the server-side boundary**

In `get_current_agent_conversation`, treat the latest run as current only when it is active or there is no later user message:

```python
post_terminal_user_message = (
    run is not None
    and run.status in _TERMINAL_STATUSES
    and any(
        message.role == "user" and message.created_at > run.updated_at
        for message in messages
    )
)
visible_run = None if post_terminal_user_message else run
```

Use `visible_run` for confirmation and `task` response construction.

- [ ] **Step 4: Write frontend failing regression test**

Hydrate a completed task, send the next ordinary message, and assert:

```typescript
expect(screen.getByLabelText("Agent 任务进度")).toBeInTheDocument();
await user.type(screen.getByLabelText("对账目标"), "开始下一轮同步");
await user.click(screen.getByRole("button", { name: "发送" }));
expect(screen.queryByLabelText("Agent 任务进度")).not.toBeInTheDocument();
```

- [ ] **Step 5: Implement immediate frontend clearing**

Before sending an ordinary message, clear the local task only when it is terminal:

```typescript
if (task && terminalTaskStatuses.has(task.status)) {
  setTask(undefined);
}
```

Do not apply this branch to active clarification messages.

- [ ] **Step 6: Run focused tests**

Run:

```bash
backend/.venv/bin/pytest backend/tests/integration/api/test_agent_api.py -q -k "terminal"
cd frontend && npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: all selected tests pass.

### Task 2: Compact conversation and align task history

**Files:**
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/tasks/TaskListPage.tsx`
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/styles/apple.css`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`
- Test: `frontend/src/features/tasks/TaskListPage.test.tsx`
- Test: `frontend/src/app/App.test.tsx`

**Interfaces:**
- Produces: `.conversation-page-actions` compact action row and `.task-list-heading` padded card header.

- [ ] **Step 1: Write failing semantic tests**

Assert the conversation page has no `h1` named “新建对话”, still exposes the “新建对话” region and “开启新对话” button, and renders the history title inside `.task-list-heading`.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx src/features/tasks/TaskListPage.test.tsx src/app/App.test.tsx
```

Expected: FAIL on the old heading and missing history class.

- [ ] **Step 3: Implement compact markup**

Replace the large heading with:

```tsx
<div className="conversation-page-actions">
  <button className="conversation-reset-button">...</button>
</div>
```

Add `task-list-heading` to the history title row.

- [ ] **Step 4: Implement scoped spacing**

Give the action row a compact height, expand `.conversation-surface`, and add matching horizontal padding to `.task-list-heading`. Update mobile rules without altering shared `.section-title-row` layouts.

- [ ] **Step 5: Run focused tests**

Expected: all selected tests pass with no accessibility regression.

### Task 3: Normalize manual-sync setting cards

**Files:**
- Modify: `frontend/src/features/task-create/TaskCreatePage.tsx`
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/styles/apple.css`
- Test: `frontend/src/features/task-create/TaskCreatePage.test.tsx`
- Test: `frontend/src/styles/pageThemeCoverage.test.ts`

**Interfaces:**
- Produces: four `.sync-setting-card` elements inside `.sync-settings-grid`.

- [ ] **Step 1: Write failing structure test**

Open manual sync and assert:

```typescript
expect(container.querySelectorAll(".sync-setting-card")).toHaveLength(4);
```

Also assert each select and task-name input remains reachable by its existing accessible label.

- [ ] **Step 2: Verify test fails**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/TaskCreatePage.test.tsx
```

Expected: FAIL because no setting-card class exists.

- [ ] **Step 3: Apply setting-card markup and CSS**

Add `sync-setting-card` to the task label and three fieldsets. Use:

```css
.sync-setting-card {
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--codex-border);
  border-radius: 10px;
}

.sync-setting-card input,
.sync-setting-card select {
  width: 100%;
  min-width: 0;
  max-width: 100%;
}
```

Keep the two-column desktop and one-column mobile grid.

- [ ] **Step 4: Run frontend quality gates and visual check**

Run:

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Inspect conversation, task history, and manual-sync pages at desktop and mobile widths. Confirm there is no horizontal overflow or clipped label text.

### Task 4: Final verification and integration

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run backend suite**

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

- [ ] **Step 2: Run repository checks**

```bash
git diff --check
openspec validate --all --strict --no-interactive
```

- [ ] **Step 3: Request code review**

Review terminal task time comparison, timezone handling, accessibility, responsive overflow, and unrelated changes.

- [ ] **Step 4: Commit and merge after all checks pass**

Use Conventional Commit messages, merge into `master`, rerun focused tests on the merged result, then remove the owned `.worktrees/fix-conversation-sync-layout` worktree.

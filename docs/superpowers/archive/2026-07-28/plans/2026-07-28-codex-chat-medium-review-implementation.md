# Codex Chat Medium-Risk Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Codex-style wide conversation layout, in-chat medium-risk bulk review, and terminal-task conversation continuation.

**Architecture:** A focused `ConversationMediumRiskReviewCard` owns per-Finding default/reject UI state while `ConversationCreatePage` owns Graph polling and API submission. Terminal continuation is implemented at both the frontend state boundary and backend Conversation restoration boundary so navigation and reload preserve the next start confirmation.

**Tech Stack:** React 19, TypeScript, Ant Design, Vitest/Testing Library, FastAPI, SQLAlchemy, pytest.

## Global Constraints

- Medium-risk items are default-approved and submitted through the existing frozen batch Gate API.
- High-risk approval and identity clarification behavior must remain unchanged.
- Ordinary input stays locked while a task is active.
- Terminal tasks preserve conversation history and permit the next sequential task.
- No new database migration or dependency is introduced.

---

### Task 1: Chat medium-risk bulk review

**Files:**
- Create: `frontend/src/features/task-create/ConversationMediumRiskReviewCard.tsx`
- Create: `frontend/src/features/task-create/ConversationMediumRiskReviewCard.test.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

**Interfaces:**
- Consumes: `AgentGraphHumanGate[]`, current Graph cursor, and `AgentConversationApi.decideGraphGates`.
- Produces: `ConversationMediumRiskReviewCard({ gates, onSubmit })`, where `onSubmit(gates, rejectedFindingIds)` returns per-Gate completion statuses.

- [ ] **Step 1: Write the failing component test**

```tsx
render(<ConversationMediumRiskReviewCard gates={mediumGates} onSubmit={onSubmit} />);
expect(screen.getByText("中风险治理建议")).toBeInTheDocument();
expect(screen.getByRole("button", { name: "全部同意并继续" })).toBeInTheDocument();
await user.click(screen.getByRole("checkbox", { name: "拒绝张老师" }));
await user.click(screen.getByRole("button", { name: /同意 1，拒绝 1/ }));
expect(onSubmit).toHaveBeenCalledWith(mediumGates, new Set(["finding-1"]));
```

- [ ] **Step 2: Run the component test and verify RED**

Run: `cd frontend && npm test -- --run src/features/task-create/ConversationMediumRiskReviewCard.test.tsx`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement the card and Conversation batch callback**

```ts
const decisions = gates.map((gate) => ({
  gate_id: gate.id,
  decision: approvedIds.length ? "approve" : "reject",
  approved_finding_ids: approvedIds,
  rejected_finding_ids: rejectedIds,
  graph_cursor: graphCursor,
  membership_hash: gate.membership_hash!,
}));
return backendApi.decideGraphGates(task.id, decisions);
```

- [ ] **Step 4: Add the page-level failing test, then render all medium Gates as one card**

```tsx
expect(await screen.findByRole("region", { name: "中风险批量审核" })).toBeInTheDocument();
expect(screen.getAllByRole("checkbox", { name: /拒绝/ })).toHaveLength(2);
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `cd frontend && npm test -- --run src/features/task-create/ConversationMediumRiskReviewCard.test.tsx src/features/task-create/ConversationCreatePage.test.tsx`

Expected: all focused tests pass.

### Task 2: Terminal conversation continuation

**Files:**
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`
- Modify: `backend/app/api/routes/agent.py`
- Modify: `backend/tests/integration/api/test_agent_api.py`

**Interfaces:**
- Consumes: existing `terminalTaskStatuses`, Conversation context, and latest `AgentRunRecord`.
- Produces: terminal-aware composer/start confirmation behavior without changing API schemas.

- [ ] **Step 1: Write failing frontend tests**

```tsx
expect(await screen.findByText("任务已完成")).toBeInTheDocument();
expect(screen.getByLabelText("对账目标")).toBeEnabled();
await user.type(screen.getByLabelText("对账目标"), "再同步一次教师数据");
expect(await screen.findByRole("button", { name: "确认开始同步" })).toBeInTheDocument();
```

- [ ] **Step 2: Verify frontend RED**

Run: `cd frontend && npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx -t "continues the same conversation after a terminal task"`

Expected: FAIL because `composerLocked` treats every task as active and confirmation is hidden.

- [ ] **Step 3: Write failing backend restoration test**

```python
current = client.get("/api/agent/conversations/current")
assert current.json()["task"]["status"] == "completed"
assert current.json()["start_confirmation"]["title"] == "下一次教师同步"
```

- [ ] **Step 4: Verify backend RED**

Run: `cd backend && .venv/bin/pytest tests/integration/api/test_agent_api.py -k terminal_conversation -q`

Expected: FAIL because `can_confirm` requires `run is None`.

- [ ] **Step 5: Implement terminal-aware frontend and backend conditions**

```ts
const taskActive = Boolean(task && !terminalTaskStatuses.has(task.status));
const composerLocked = taskActive && !clarificationOpen;
const canStartConfirmedTask = !task || terminalTaskStatuses.has(task.status);
```

```python
latest_run_is_active = run is not None and run.status in ACTIVE_RUN_STATUSES
can_confirm = not latest_run_is_active and context_decision_is_confirmation
```

- [ ] **Step 6: Run focused frontend and backend tests**

Expected: terminal chat, restored confirmation, and second sequential task tests pass.

### Task 3: Codex-style wide conversation layout

**Files:**
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/styles/pageThemeCoverage.test.ts`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

**Interfaces:**
- Consumes: existing `TaskStatusRail`, Conversation message roles, and workspace sidebar.
- Produces: wide two-column conversation workspace with left/right message alignment and fixed right status rail.

- [ ] **Step 1: Write failing semantic and style tests**

```tsx
expect(screen.getByRole("article", { name: "你的消息" })).toHaveClass("user");
expect(screen.getByRole("complementary", { name: "任务处理状态" })).toBeInTheDocument();
```

```ts
expect(css).toContain("width: min(calc(100% - 32px), 1440px)");
expect(css).toContain(".conversation-message.user");
expect(css).toContain("justify-self: end");
```

- [ ] **Step 2: Verify layout tests RED**

Run: `cd frontend && npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx src/styles/pageThemeCoverage.test.ts`

- [ ] **Step 3: Implement approved layout**

```css
.conversation-create-page {
  width: min(calc(100% - 32px), 1440px);
}
.conversation-workspace.has-task-status {
  grid-template-columns: minmax(0, 1fr) 280px;
}
.conversation-message.user {
  width: fit-content;
  max-width: min(720px, 78%);
  justify-self: end;
}
```

- [ ] **Step 4: Run complete verification**

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
```

Expected: all commands exit 0.

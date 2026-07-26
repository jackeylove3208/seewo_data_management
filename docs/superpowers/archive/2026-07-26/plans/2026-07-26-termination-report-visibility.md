# Termination Report Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make task termination unmistakable and move the generated report entry to the top of the task detail page.

**Architecture:** Expose the durable `termination_requested` graph fact through the existing progress API. Derive a termination presentation state in `AgentTaskDetailPage`, render either an in-progress termination notice or a completed report card directly below the page heading, and keep the event list as audit-only content.

**Tech Stack:** FastAPI, Pydantic, React, TypeScript, TanStack Query, Ant Design, Vitest, Testing Library.

## Global Constraints

- Do not change termination state-machine semantics or automatically roll back successful mutations.
- UI state must use backend-persisted `termination_requested`, graph node, task status, and `report_id`.
- Preserve the existing Apple-style dark visual language.
- Implement behavior with failing tests first.

---

### Task 1: Expose durable termination intent

**Files:**
- Modify: `backend/app/schemas/agent_graph_api.py`
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/integration/api/test_agent_graph_api.py`
- Modify: `frontend/src/api/agent.ts`

**Interfaces:**
- Produces: `AgentGraphProgressResponse.termination_requested: bool`
- Consumes: `AgentGraphRunRecord.termination_requested`

- [ ] **Step 1: Write the failing API test**

Extend the existing blocked-run termination test to fetch `/api/agent/tasks/{task_id}/graph`
after approval and assert:

```python
assert response.json()["termination_requested"] is True
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_agent_graph_api.py -k "blocked_run_can_confirm_termination" -q
```

Expected: failure because `termination_requested` is absent.

- [ ] **Step 3: Add the response field**

Add `termination_requested: bool` to `AgentGraphProgressResponse`, populate it from
`graph.termination_requested`, and mirror the required boolean in `AgentGraphProgress`.

- [ ] **Step 4: Run the focused backend test**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/agent_graph_api.py backend/app/api/routes/agent.py \
  backend/tests/integration/api/test_agent_graph_api.py frontend/src/api/agent.ts
git commit -m "feat: expose graph termination intent"
```

### Task 2: Render termination notice and top report card

**Files:**
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.tsx`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx`
- Modify: `frontend/src/styles/apple.css`

**Interfaces:**
- Consumes: `AgentTask.status`, `AgentTask.task_kind`, `AgentTask.report_id`,
  `AgentGraphProgress.termination_requested`, and `AgentGraphProgress.current_node`
- Produces: `.agent-termination-notice` and `.agent-report-summary-card`

- [ ] **Step 1: Write failing component tests**

Add tests that assert:

```tsx
expect(screen.getByRole("heading", { name: "任务已终止" })).toBeInTheDocument();
expect(screen.getByText("仍在为你生成终止报告")).toBeInTheDocument();
expect(screen.getByText("生成终止报告")).toBeInTheDocument();
```

for a graph with `termination_requested: true` and no report, and:

```tsx
expect(screen.getByRole("heading", { name: "终止报告已生成" })).toBeInTheDocument();
expect(screen.getByRole("button", { name: "查看任务报告" })).toBeInTheDocument();
```

for a terminated task with `report_id`.

- [ ] **Step 2: Run the focused frontend test and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/features/task-detail/AgentTaskDetailPage.test.tsx
```

Expected: missing heading and report card assertions fail.

- [ ] **Step 3: Implement the presentation state**

Derive `terminationRequested` from the graph fact, termination nodes, or final
`terminated` status. Use a termination-specific final phase label and render the notice or
report card immediately after `.detail-heading`. Remove the report button after the event list.

- [ ] **Step 4: Add Apple-style card CSS**

Style both cards with the existing dark blue glass background, a restrained cyan accent for
the in-progress notice, and a green accent for the completed report card.

- [ ] **Step 5: Run focused frontend tests**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/task-detail/AgentTaskDetailPage.tsx \
  frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx \
  frontend/src/styles/apple.css
git commit -m "feat: surface termination reports above task progress"
```

### Task 3: Full verification

**Files:**
- Verify only; no production files expected.

**Interfaces:**
- Consumes: completed Tasks 1 and 2.
- Produces: verified branch ready for merge.

- [ ] **Step 1: Run backend quality gates**

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

Expected: all available tests and static checks pass.

- [ ] **Step 2: Run frontend quality gates**

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: all commands pass.

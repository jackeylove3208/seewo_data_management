# Dashboard And Conversation Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct dashboard task metrics and render actionable SQL high-risk changes inside the Agent conversation.

**Architecture:** The backend history projection supplies termination and live finding facts. Frontend task-history mapping preserves terminal states, the dashboard derives metrics from task kind and state, and the conversation polls the existing controlled-graph endpoint to render a small high-risk gate component.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Pydantic, React, TypeScript, Vitest, pytest.

## Global Constraints

- Do not change Agent matching, governance compilation, execution, audit, or rollback semantics.
- Use `OperatorContext.tenant_id`; never accept tenant identity from the client.
- Approval decisions must use the frozen graph cursor and membership hash.
- Deleted tasks are absent from history and therefore absent from every metric.
- Follow red-green-refactor for every behavior change.

---

### Task 1: Project live history facts

**Files:**
- Modify: `backend/app/schemas/agent_api.py`
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/integration/api/test_agent_api.py`

**Interfaces:**
- Produces: `AgentHistoryItem.termination_requested: bool`
- Produces: `issue_summary.total` from report facts when present, otherwise persisted `AgentFindingRecord` count.

- [ ] **Step 1: Write the failing history projection test**

Create one active graph task with two findings and `termination_requested=True`, plus one completed task with report findings. Assert `/api/agent/history` returns the active item with `termination_requested=true` and total `2`, while the completed item uses its report total.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
.venv/bin/pytest tests/integration/api/test_agent_api.py -k "history_projects_live_findings_and_termination" -q
```

Expected: failure because `termination_requested` is absent and active issue total is `0`.

- [ ] **Step 3: Implement the history projection**

Add count and graph subqueries to the existing history query. Return:

```python
termination_requested=bool(graph_termination_requested),
issue_summary={
    "total": (
        len(report.facts.get("findings", []))
        if report is not None
        else int(live_finding_count or 0)
    ),
    "excluded": len(report.facts.get("excluded_findings", [])) if report else 0,
}
```

- [ ] **Step 4: Run focused and API tests**

```bash
.venv/bin/pytest tests/integration/api/test_agent_api.py -q
```

Expected: all tests pass.

### Task 2: Correct dashboard status and metrics

**Files:**
- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/types/domain.ts`
- Modify: `frontend/src/data/taskHistory.ts`
- Modify: `frontend/src/features/tasks/TaskListPage.tsx`
- Test: `frontend/src/features/tasks/TaskListPage.test.tsx`
- Test: `frontend/src/data/taskHistory.test.ts`

**Interfaces:**
- Consumes: `AgentHistoryItem.termination_requested`
- Produces: `TaskStatus = "ready" | "processing" | "terminated" | "failed"`

- [ ] **Step 1: Write failing mapping and metric tests**

Cover these rows:

```ts
[
  { taskKind: "sync", status: "ready", issueCount: 8 },
  { taskKind: "sync", status: "processing", issueCount: 3 },
  { taskKind: "sync", status: "terminated", issueCount: 4 },
  { taskKind: "sync", status: "failed", issueCount: 2 },
  { taskKind: "rollback", status: "ready", issueCount: 10 },
]
```

Assert completed count `2` only for visual history, governance success rate `25%` from one completed sync out of four sync tasks, and pending issues `9` from processing + terminated + failed. Assert a backend running task with `termination_requested=true` maps to `terminated`.

- [ ] **Step 2: Verify RED**

```bash
npm test -- --run src/data/taskHistory.test.ts src/features/tasks/TaskListPage.test.tsx
```

Expected: terminated maps to ready, success rate uses operation totals, and completed issues are included.

- [ ] **Step 3: Implement status and metric selectors**

Map effective status before rendering. Calculate:

```ts
const syncTasks = tasks.filter((task) => task.taskKind !== "rollback");
const completedSyncCount = syncTasks.filter((task) => task.status === "ready").length;
const operationSuccessRate = syncTasks.length
  ? `${Math.round((completedSyncCount / syncTasks.length) * 100)}%`
  : "暂无数据";
const issueCount = tasks
  .filter((task) => task.status !== "ready")
  .reduce((sum, task) => sum + task.issueCount, 0);
```

Add the “已终止” label and non-processing tag color.

- [ ] **Step 4: Run focused frontend tests**

```bash
npm test -- --run src/data/taskHistory.test.ts src/features/tasks/TaskListPage.test.tsx
```

Expected: all tests pass.

### Task 3: Render controlled high-risk gates in conversation

**Files:**
- Create: `frontend/src/features/task-create/ConversationRiskApprovalCard.tsx`
- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/styles/global.css`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

**Interfaces:**
- Consumes: `AgentConversationApi.graph?(taskId, signal)`
- Consumes: `AgentGraphHumanGate` where `kind === "high_risk_approval"` and `risk === "high"`
- Uses: `decideGraphGate(taskId, gateId, decision, reason, review)`

- [ ] **Step 1: Write failing conversation approval tests**

Return a graph response with a pending high-risk gate containing one teacher deletion and one field change. Assert the chat shows person, operation, and `before → after`, hides `analysis_zh` and `solution_zh`, and submits cursor plus membership hash. Add rejection and decided-state assertions.

- [ ] **Step 2: Verify RED**

```bash
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx -t "SQL high-risk"
```

Expected: no high-risk detail card exists.

- [ ] **Step 3: Implement graph polling and the compact card**

Extend `AgentConversationApi` with optional `graph`. Poll while a graph task is active or awaiting approval. Render only high-risk gates. Build exact review payload:

```ts
{
  approved_finding_ids: decision === "approve" ? findingIds : [],
  rejected_finding_ids: decision === "reject" ? findingIds : [],
  graph_cursor: gate.cursor,
  membership_hash: gate.membership_hash,
}
```

Update local gate state only after the request succeeds.

- [ ] **Step 4: Run focused frontend tests**

```bash
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: all tests pass.

### Task 4: Full verification

**Files:**
- Verify only.

**Interfaces:**
- Consumes all prior task outputs.

- [ ] **Step 1: Run backend checks**

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

- [ ] **Step 2: Run frontend checks**

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

- [ ] **Step 3: Confirm clean diff**

```bash
git diff --check
git status --short
```


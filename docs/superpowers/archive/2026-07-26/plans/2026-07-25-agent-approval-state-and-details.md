# Agent Approval State and Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make high-risk approval gates appear only at the correct graph cursor, show exact masked operation details, and allow approved work to proceed into governance without stale-cursor or persistence errors.

**Architecture:** Separate the side-effect-free transition into risk aggregation from the aggregation action itself, then expose server-computed gate actionability and frozen finding details through the progress API. Keep the React client subordinate to server state: it renders exact details, disables stale gates, and maps graph transitions to Chinese business progress.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async ORM, PostgreSQL/SQLite tests, pytest, React, TypeScript, TanStack Query, Vitest.

## Global Constraints

- Only delete operations and updates to an existing student's phone are high risk.
- Student phone values and contact details must be masked in operator-facing responses.
- Approval decisions remain bound to the server-frozen finding membership.
- Existing failed tasks are not automatically resumed.
- The three-step reconciliation algorithm and model batching remain unchanged.
- Production code is written only after the corresponding test fails for the expected reason.

---

### Task 1: Separate graph transition from risk aggregation

**Files:**
- Modify: `backend/app/agent_graph/definition.py`
- Modify: `backend/app/agent_graph/runtime.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Test: `backend/tests/unit/agent_graph/test_definition.py`
- Test: `backend/tests/integration/agent_graph/test_production_runtime.py`

**Interfaces:**
- Produces: `enter_aggregate_risk`, a side-effect-free action whose successor is `aggregate_risk`.
- Preserves: `aggregate_risk`, which is valid only at the `aggregate_risk` node and may pause at `wait_high_risk_approvals`.

- [ ] **Step 1: Write failing graph-definition and executor tests**

```python
def test_analysis_enters_risk_aggregation_without_reusing_side_effect_action() -> None:
    graph = agent_sync_graph_v1()
    node = graph.node("analyze_actionable_batches")
    assert ("enter_aggregate_risk", "aggregate_risk") in {
        (edge.action_id, edge.successor_node) for edge in node.actions
    }
    assert ("aggregate_risk", "aggregate_risk") not in {
        (edge.action_id, edge.successor_node) for edge in node.actions
    }


@pytest.mark.asyncio
async def test_enter_aggregate_risk_is_a_guarded_noop(database, tmp_path: Path) -> None:
    context = replace(
        await _preflight_context(database, tmp_path),
        current_node="analyze_actionable_batches",
    )
    action = AllowedActionV1(
        action_id="enter_aggregate_risk",
        graph_action_kind="enter_aggregate_risk",
        successor_node="aggregate_risk",
    )
    outcome = await _executor(database)(context, action)
    assert outcome.pause_for_human is False
    assert await _approval_gate_count(database, context.graph_run_id) == 0
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
../backend/.venv/bin/pytest tests/unit/agent_graph/test_definition.py tests/integration/agent_graph/test_production_runtime.py -q
```

Expected: failures because `enter_aggregate_risk` is not declared and the old action dispatches `_aggregate_risk`.

- [ ] **Step 3: Implement distinct actions and node-aware dispatch**

```python
if action_kind == "enter_aggregate_risk":
    return await self._record_guarded_noop(context, action)
if action_kind == "aggregate_risk":
    if context.current_node != "aggregate_risk":
        raise GraphGuardRejected("aggregate_risk_action_outside_aggregate_node")
    return await self._aggregate_risk(context, action)
```

Replace analysis and conflict-resolution edges/templates with `enter_aggregate_risk`; retain `aggregate_risk` only on the actual aggregation node. Update rejection-reason mappings to use the new action ID.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2.

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_graph backend/tests/unit/agent_graph/test_definition.py backend/tests/integration/agent_graph/test_production_runtime.py
git commit -m "fix: separate risk aggregation graph transition"
```

### Task 2: Make deterministic aggregation replay-safe

**Files:**
- Modify: `backend/app/agent_graph/production_executor.py`
- Modify: `backend/app/agent_graph/human_gates.py`
- Test: `backend/tests/integration/agent_graph/test_production_runtime.py`
- Test: `backend/tests/integration/agent_graph/test_human_gates.py`

**Interfaces:**
- Preserves: one deterministic invocation per `(graph_run_id, cursor, action_id, skill_name, attempt)`.
- Produces: one frozen gate per approval group membership, even when the aggregation action is retried.

- [ ] **Step 1: Write failing replay tests**

```python
@pytest.mark.asyncio
async def test_aggregate_risk_replay_reuses_frozen_gates_and_invocation(database) -> None:
    context, action = await _aggregate_context(database)
    executor = _executor(database)
    first = await executor(context, action)
    second = await executor(context, action)
    assert first.pause_for_human is True
    assert second.pause_for_human is True
    assert await _approval_gate_count(database, context.graph_run_id) == 1
    assert await _invocation_count(database, context, action) == 1
```

- [ ] **Step 2: Run the focused replay tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph/test_production_runtime.py tests/integration/agent_graph/test_human_gates.py -q
```

Expected: duplicate deterministic invocation or duplicate gate persistence fails the replay.

- [ ] **Step 3: Implement idempotent replay**

Before inserting a deterministic invocation, query the unique invocation identity. If it exists with a completed validated output, return its ID instead of inserting. Keep hash and graph binding validation; conflicting replay content must still fail.

`freeze_high_risk_approvals` must look up a gate by graph run, cursor, gate kind and frozen membership hash before insertion and reuse the exact gate.

- [ ] **Step 4: Run replay tests and verify GREEN**

Run the command from Step 2.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_graph backend/tests/integration/agent_graph
git commit -m "fix: make risk aggregation replay safe"
```

### Task 3: Correct high-risk policy and expose frozen operation details

**Files:**
- Modify: `backend/app/governance/agent_governance.py`
- Modify: `backend/app/schemas/agent_graph_api.py`
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/unit/governance/test_agent_governance.py`
- Test: `backend/tests/integration/api/test_agent_graph_api.py`

**Interfaces:**
- Produces: `AgentGraphHumanGateView.actionable: bool`.
- Produces: `AgentGraphHumanGateView.unavailable_reason_zh: str | None`.
- Produces: `AgentGraphHumanGateView.items: tuple[AgentGraphApprovalItemView, ...]`.

- [ ] **Step 1: Write failing risk-policy tests**

```python
def test_student_create_with_phone_is_not_high_risk() -> None:
    decision = AgentRiskPolicy().assess(
        finding(kind="target_missing", operation="create", fields=("phone",))
    )
    assert decision.risk == "medium"
    assert decision.requires_approval is False
```

Retain assertions that delete and student phone update are high risk.

- [ ] **Step 2: Write failing progress API tests**

Seed real `AgentInputRecord`, `AgentWorkItemRecord`, `AgentFindingRecord`, recommended solution, approval group and human gate. Assert:

```python
assert gate["actionable"] is True
assert gate["unavailable_reason_zh"] is None
assert gate["items"] == [
    {
        "finding_id": finding_id,
        "entity_kind": "teacher",
        "entity_name": "王老师",
        "entity_number": "T-001",
        "class_name": None,
        "source_locator": "csv:8",
        "source_row_number": 8,
        "operation_zh": "删除希沃中的教师记录",
        "issue_zh": "希沃重复记录",
        "analysis_zh": "该教师记录与已保留记录重复。",
        "changes": [],
    }
]
```

Also seed a failed graph with a pending old gate and assert `actionable is False` with a Chinese unavailable reason.

- [ ] **Step 3: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/unit/governance/test_agent_governance.py tests/integration/api/test_agent_graph_api.py -q
```

- [ ] **Step 4: Implement policy and server-built details**

Use:

```python
high = finding.operation == AgentOperation.DELETE or (
    finding.operation == AgentOperation.UPDATE
    and finding.entity_kind == "student"
    and "phone" in finding.changed_fields
)
```

Build detail rows by joining frozen `member_ids` to findings, work items, subject inputs, identity claims and recommended solutions. Sort by input stable order and finding ID. Mask phone and email before creating response DTOs. Compute `actionable` from run status, graph node, graph cursor, gate cursor and gate status.

- [ ] **Step 5: Run tests and verify GREEN**

Run the command from Step 3.

- [ ] **Step 6: Commit**

```bash
git add backend/app/governance/agent_governance.py backend/app/schemas/agent_graph_api.py backend/app/api/routes/agent.py backend/tests
git commit -m "feat: expose actionable approval details"
```

### Task 4: Render exact approval details and Chinese graph progress

**Files:**
- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.tsx`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx`
- Modify: `frontend/src/features/agent-events/presentation.ts`
- Modify: `frontend/src/features/agent-events/presentation.test.ts`
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/styles/apple.css`

**Interfaces:**
- Consumes: `actionable`, `unavailable_reason_zh` and `items` from Task 3.
- Produces: an expandable detail list and safe disabled state for stale gates.

- [ ] **Step 1: Write failing React tests**

```tsx
expect(screen.getByText("删除教师：王老师（编号 T-001）")).toBeInTheDocument();
expect(screen.getByText("希沃第 8 行")).toBeInTheDocument();
expect(screen.getByRole("button", { name: "同意" })).toBeEnabled();
```

For an old gate:

```tsx
expect(screen.getByText("审批不可用")).toBeInTheDocument();
expect(screen.queryByRole("button", { name: "同意" })).not.toBeInTheDocument();
```

For a `graph.transitioned` event:

```ts
expect(
  presentAgentEvent(graphEvent("normalize_next_batch")).title,
).toBe("数据规范化批次已完成");
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd frontend
npm test -- --run src/features/task-detail/AgentTaskDetailPage.test.tsx src/features/agent-events/presentation.test.ts
```

- [ ] **Step 3: Implement typed rendering**

Extend the API types with `AgentGraphApprovalItem` and `AgentGraphApprovalChange`. Render each gate item in a semantic list showing entity name, number, class/row context, operation, reason and before/after masked values. Show buttons only when `gate.actionable`.

Add a `graph.transitioned` branch that maps `payload.action_id` to Chinese labels for inspection, normalization, validation, identity indexing, work construction, analysis, risk aggregation, approval waiting, governance execution and reporting.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat: show exact approval operations"
```

### Task 5: Verify approval-to-governance lifecycle

**Files:**
- Modify: `backend/tests/e2e/test_agent_graph_lifecycle.py`
- Modify: `frontend/tests/e2e/agent-graph.spec.ts` only if the existing fixture contract needs the new fields.

**Interfaces:**
- Verifies the completed behavior from Tasks 1–4.

- [ ] **Step 1: Add a lifecycle regression test**

The test must run a graph from completed analysis through `enter_aggregate_risk`, aggregate once, approve the current gate, compile the plan and enter governance. Assert:

```python
assert graph.current_node == "wait_high_risk_approvals"
assert run.status == "waiting_human"
assert progress.business_stage == "governance_execution"
assert decision.status_code == 200
assert graph_after_decision.current_node in {
    "compile_execution_plan",
    "preflight_execution",
    "execute_ready_operations",
}
assert run_after_decision.status != "failed"
```

- [ ] **Step 2: Run the lifecycle test and fix only integration defects**

```bash
cd backend
.venv/bin/pytest tests/e2e/test_agent_graph_lifecycle.py -q
```

- [ ] **Step 3: Run backend quality gates**

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

Expected: all tests pass, with only the documented migration smoke-test skip when its PostgreSQL test URL is absent; Ruff and mypy exit zero.

- [ ] **Step 4: Run frontend quality gates**

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: all commands exit zero.

- [ ] **Step 5: Commit final test adjustments**

```bash
git add backend/tests frontend/tests
git commit -m "test: cover approval governance transition"
```

- [ ] **Step 6: Review branch scope**

```bash
git status --short
git diff --check master...HEAD
git log --oneline master..HEAD
```

Expected: only plan, backend approval/state changes, frontend approval/progress changes and their tests are present.

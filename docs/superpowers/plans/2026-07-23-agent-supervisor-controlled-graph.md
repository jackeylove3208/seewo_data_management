# AI Supervisor Controlled Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-capable `agent-graph-v1` workflow in which a real AI Supervisor chooses among server-derived actions and versioned Skill sub-agents produce bounded structured evidence, while the backend retains all safety and write authority.

**Architecture:** Preserve `legacy-v1` and the fixed `new-agent-v1` runtime unchanged. Add a separate graph runtime under `app/agent_graph` with immutable graph definitions, complete candidate evaluation, strict decision validation, append-only audit persistence, evidence manifests and phase-scoped tools. Reuse existing deterministic ingestion, identity, governance, reporting and rollback services only behind typed action executors; remove legacy delegation from the final `agent-graph-v1` normal path.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy async, Alembic, PostgreSQL, httpx, pytest, React, TypeScript, TanStack Query, Vitest and Playwright.

## Global constraints

- `legacy-v1`, `new-agent-v1` and `agent-graph-v1` are separate immutable workflow versions.
- `OperatorContext.tenant_id` is the only tenant source; clients cannot submit or override it.
- Server code owns graph definitions, candidate evaluation, guards, risk, approvals, operation compilation, target writes, audit facts and rollback facts.
- A Supervisor decision can select only an action in the exact server-issued `allowed_actions` set.
- A decision with multiple safe candidates exposes at least two semantically distinct actions; an audited singleton is allowed only for an enumerated safety reason.
- The model receives no arbitrary SQL, filesystem path, URL, Shell, credential or direct source/target write capability.
- Every model-visible object belongs to a versioned evidence manifest; student phone values remain tokenized.
- Model calls use an initial attempt plus at most three retries, with no silent fallback to legacy output.
- Correct records remain silent; every actionable finding receives Chinese AI analysis and a governance proposal.
- Migrations are additive and based on the current Alembic head; existing migrations are never edited.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/app/agent_graph/contracts.py` | Strict graph, action, Supervisor decision, evidence and gate contracts. |
| `backend/app/agent_graph/definition.py` | Immutable sync/rollback graph definitions and graph-version lookup. |
| `backend/app/agent_graph/actions.py` | Complete candidate evaluation, distinctness and singleton-policy enforcement. |
| `backend/app/agent_graph/guards.py` | Tenant, cursor, lease, lock, evidence, approval and retry guards. |
| `backend/app/agent_graph/repository.py` | Append-only graph, decision, transition, invocation, tool and gate persistence. |
| `backend/app/agent_graph/supervisor.py` | Model call, decision validation and provenance persistence. |
| `backend/app/agent_graph/evidence.py` | Evidence manifest issuance, membership validation and privacy projection. |
| `backend/app/agent_graph/tools.py` | Phase-scoped MCP façade and tool-call audit. |
| `backend/app/agent_graph/executors.py` | Dispatches typed actions to sub-agent or deterministic executors. |
| `backend/app/agent_graph/worker.py` | Lease-safe graph loop, retries, recovery, transition and terminal handling. |
| `backend/app/models/agent_graph.py` | Additive SQLAlchemy audit and graph runtime records. |
| `backend/app/schemas/agent_graph_api.py` | Typed API/event/human-gate responses. |
| `backend/app/ai/graph_supervisor.py` | Versioned Supervisor Skill request using the configured model gateway. |
| `backend/app/ai/graph_subagents.py` | Real Skill model invocations for inspection, normalization and analysis. |
| `backend/alembic/versions/0025_agent_supervisor_graph.py` | New graph tables, constraints and indexes. |
| `backend/tests/unit/agent_graph/*` | Contracts, choice, graph, guard, evidence and decision tests. |
| `backend/tests/integration/agent_graph/*` | Persistence, worker, real sub-agent boundary and recovery tests. |
| `frontend/src/api/agent.ts` | Graph event, action, progress and human-gate client contracts. |
| `frontend/src/features/task-detail/AgentTaskDetailPage.tsx` | Business-readable graph progress and approvals. |

## Task 1: Workflow flag and immutable version routing

**Files:**

- Modify: `backend/app/core/config.py`
- Modify: `backend/app/agent_runtime/task_service.py`
- Modify: `backend/app/api/routes/agent.py`
- Modify: `backend/app/schemas/agent_api.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/unit/core/test_config.py`
- Test: `backend/tests/integration/api/test_agent_api.py`

**Interfaces:**

- Produces `Settings.agent_graph_enabled: bool`.
- Produces `Settings.agent_graph_csv_execution_enabled: bool`.
- Produces `Settings.new_task_workflow_version -> Literal["legacy-v1", "new-agent-v1", "agent-graph-v1"]`.
- Existing tasks retain their persisted `workflow_version` regardless of later flag changes.

- [ ] **Step 1: Add failing routing and configuration tests**

```python
def test_graph_flag_routes_only_new_tasks() -> None:
    settings = Settings(new_agent_enabled=True, agent_graph_enabled=True)
    assert settings.new_task_workflow_version == "agent-graph-v1"

def test_graph_execution_requires_graph_flag() -> None:
    with pytest.raises(ValueError, match="agent_graph_enabled"):
        Settings(agent_graph_csv_execution_enabled=True)
```

Add an API characterization proving an existing `new-agent-v1` task is still returned with that exact version after graph flags are enabled.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/unit/core/test_config.py tests/integration/api/test_agent_api.py -q`

Expected: FAIL because graph flags and the `agent-graph-v1` schema literal do not exist.

- [ ] **Step 3: Implement minimum version routing**

```python
agent_graph_enabled: bool = False
agent_graph_csv_execution_enabled: bool = False

@property
def new_task_workflow_version(self) -> str:
    if self.new_agent_enabled and self.agent_graph_enabled:
        return "agent-graph-v1"
    return "new-agent-v1" if self.new_agent_enabled else "legacy-v1"
```

Validate that graph execution requires both `new_agent_enabled` and `agent_graph_enabled`. Replace creation-time hard-coded `"new-agent-v1"` with the property, but keep all read paths version-aware.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest tests/unit/core/test_config.py tests/integration/api/test_agent_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/app/agent_runtime/task_service.py backend/app/api/routes/agent.py backend/app/schemas/agent_api.py backend/.env.example backend/tests
git commit -m "feat: route new tasks to agent graph workflow"
```

## Task 2: Graph and Supervisor contracts with real action choice

**Files:**

- Create: `backend/app/agent_graph/__init__.py`
- Create: `backend/app/agent_graph/contracts.py`
- Create: `backend/app/agent_graph/definition.py`
- Create: `backend/app/agent_graph/actions.py`
- Test: `backend/tests/unit/agent_graph/test_contracts.py`
- Test: `backend/tests/unit/agent_graph/test_action_selection.py`
- Test: `backend/tests/unit/agent_graph/test_definition.py`

**Interfaces:**

- Produces `AllowedActionV1`, `CandidateActionEvaluationV1`, `SupervisorContextV1` and `SupervisorDecisionV1`.
- Produces `GraphDefinition.allowed_candidates(node, facts)`.
- Produces `build_allowed_action_set(candidates) -> AllowedActionSetV1`.
- Raises `InvalidActionSet` for incomplete projections, aliases or unjustified singleton choices.

- [ ] **Step 1: Add failing choice-contract tests**

```python
def test_multiple_safe_candidates_are_all_exposed() -> None:
    result = build_allowed_action_set(
        (passed("inspect_source"), passed("terminate_task"), rejected("execute", "approval_missing"))
    )
    assert [item.action_id for item in result.allowed_actions] == [
        "inspect_source",
        "terminate_task",
    ]

def test_alias_actions_cannot_fake_choice() -> None:
    with pytest.raises(InvalidActionSet, match="semantic alias"):
        build_allowed_action_set((passed("a", fingerprint="same"), passed("b", fingerprint="same")))

def test_singleton_requires_server_reason() -> None:
    with pytest.raises(InvalidActionSet, match="single_action_reason_code"):
        build_allowed_action_set((passed("only"),))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/unit/agent_graph/test_contracts.py tests/unit/agent_graph/test_action_selection.py tests/unit/agent_graph/test_definition.py -q`

Expected: FAIL because `app.agent_graph` does not exist.

- [ ] **Step 3: Implement strict immutable contracts**

```python
class SupervisorDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: str
    reason_zh: str = Field(min_length=1, max_length=1000)
    expected_result: str = Field(min_length=1, max_length=256)
    observed_blockers: tuple[str, ...] = ()
    risk_notes_zh: tuple[str, ...] = ()
    why_not_other_actions_zh: tuple[UnselectedActionReasonV1, ...] = ()
    operator_message_zh: str | None = Field(default=None, max_length=1000)
```

Hash canonical serialized allowed actions. Define semantic fingerprints from action kind, sub-agent, resource IDs, required evidence and legal successor. Validate exact alternative coverage and server-member blocker/evidence references.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest tests/unit/agent_graph -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_graph backend/tests/unit/agent_graph
git commit -m "feat: define controlled agent graph contracts"
```

## Task 3: Append-only graph persistence and migration

**Files:**

- Create: `backend/app/models/agent_graph.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/app/agent_graph/repository.py`
- Create: `backend/alembic/versions/0025_agent_supervisor_graph.py`
- Test: `backend/tests/integration/agent_graph/test_repository.py`
- Modify: `backend/tests/integration/test_migrations.py`

**Interfaces:**

- Produces `AgentGraphRepository.create_run_state()`, `record_candidate_set()`, `record_decision()`, `record_transition()`, `record_manifest()`, `record_invocation()`, `record_tool_call()` and `record_human_gate()`.
- Completed decision, transition, invocation and tool-call rows are immutable.
- Graph cursor uses optimistic compare-and-swap.

- [ ] **Step 1: Write failing append-only and cursor tests**

```python
async def test_transition_requires_current_cursor(session) -> None:
    state = await repository.create_run_state(run_id, graph_version="agent-sync-graph-v1")
    await repository.record_transition(state.id, expected_cursor=0, to_node="inspect_sources")
    with pytest.raises(GraphCursorConflict):
        await repository.record_transition(state.id, expected_cursor=0, to_node="normalize_input_batches")

async def test_decision_persists_complete_candidate_audit(session) -> None:
    record = await repository.record_decision(context, candidate_evaluations, decision)
    assert record.action_set_hash == context.action_set_hash
    assert len(record.candidate_evaluations) == 2
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/integration/agent_graph/test_repository.py -q`

Expected: FAIL because graph models and repository are absent.

- [ ] **Step 3: Add models, constraints and repository**

Create additive tables for run state, transitions, Supervisor decisions, evidence manifests, sub-agent invocations, tool calls and human gates. Use UUID primary keys, tenant/run indexes, JSON payloads, hashes, timestamps, unique `(graph_run_id, cursor)`, and check constraints for terminal/completed facts.

- [ ] **Step 4: Verify repository and clean migration**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph/test_repository.py -q
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

Expected: PASS and Alembic head equals `0025_agent_supervisor_graph`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models backend/app/agent_graph/repository.py backend/alembic/versions/0025_agent_supervisor_graph.py backend/tests/integration
git commit -m "feat: persist agent graph audit records"
```

## Task 4: Real Supervisor model invocation and decision validation

**Files:**

- Create: `backend/app/ai/graph_supervisor.py`
- Create: `backend/app/ai/skills/orchestrate-controlled-agent-graph/SKILL.md`
- Create: `backend/app/agent_graph/supervisor.py`
- Modify: `backend/app/ai/skills/contracts.py`
- Modify: `backend/app/ai/skills/registry.py`
- Test: `backend/tests/unit/ai/test_graph_supervisor.py`
- Test: `backend/tests/integration/agent_graph/test_supervisor.py`

**Interfaces:**

- Produces `GraphSupervisorAgent.decide(context) -> SupervisorDecisionV1`.
- Produces `SupervisorDecisionService.decide_and_record(run_state)`.
- The model sees only `SupervisorContextV1`; it receives no graph edge mutation or tool capability.

- [ ] **Step 1: Add failing structured-decision tests**

```python
async def test_different_model_choices_produce_different_selected_actions() -> None:
    first = await supervisor(scripted("inspect_students")).decide(context_with_two_actions())
    second = await supervisor(scripted("inspect_teachers")).decide(context_with_two_actions())
    assert first.action_id != second.action_id

async def test_every_unselected_action_requires_a_reason() -> None:
    with pytest.raises(InvalidSupervisorDecision, match="unselected action coverage"):
        await supervisor(scripted_without_why_not()).decide(context_with_two_actions())
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/unit/ai/test_graph_supervisor.py tests/integration/agent_graph/test_supervisor.py -q`

Expected: FAIL because the controlled Supervisor Agent is absent.

- [ ] **Step 3: Implement Skill-backed model call**

Load the exact Skill name/version, build strict JSON-schema mode requests through the existing provider, validate membership and non-executing audit fields, retry only provider/schema failures, privacy-filter the operator message, and atomically persist provenance with the candidate set.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest tests/unit/ai/test_graph_supervisor.py tests/integration/agent_graph/test_supervisor.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai backend/app/agent_graph/supervisor.py backend/tests
git commit -m "feat: add skill-backed graph supervisor"
```

## Task 5: Guarded graph worker, leases and recovery

**Files:**

- Create: `backend/app/agent_graph/guards.py`
- Create: `backend/app/agent_graph/executors.py`
- Create: `backend/app/agent_graph/worker.py`
- Modify: `backend/app/agent_runtime/__main__.py`
- Modify: `backend/app/agent_runtime/service.py`
- Test: `backend/tests/unit/agent_graph/test_guards.py`
- Test: `backend/tests/integration/agent_graph/test_worker.py`

**Interfaces:**

- Produces `GraphGuardService.evaluate(action, state, facts)`.
- Produces `AgentGraphWorker.run_once() -> bool`.
- Existing `AgentWorker` continues handling only `new-agent-v1`.

- [ ] **Step 1: Add failing guard and route-divergence tests**

```python
async def test_two_supervisor_choices_dispatch_different_work(session) -> None:
    first = await run_one_decision(choice="inspect_students")
    second = await run_one_decision(choice="inspect_teachers")
    assert first.dispatched_resource_ids != second.dispatched_resource_ids
    assert first.transition.to_node != second.transition.to_node

async def test_stale_fencing_token_cannot_commit_action(session) -> None:
    with pytest.raises(GraphGuardRejected, match="stale_fencing"):
        await worker.commit_outcome(context_with_old_fencing_token(), outcome())

async def test_fourth_same_node_replan_blocks_the_run(session) -> None:
    result = await exhaust_same_node_replans(limit=3)
    assert result.status == "blocked_model_error"

async def test_cross_phase_replan_creates_a_human_gate(session) -> None:
    result = await request_replan_after_analysis()
    assert result.status == "waiting_human"
    assert result.gate.kind == "cross_phase_replan_confirmation"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/unit/agent_graph/test_guards.py tests/integration/agent_graph/test_worker.py -q`

Expected: FAIL because graph worker and guards are absent.

- [ ] **Step 3: Implement bounded graph loop**

Claim only `agent-graph-v1` runs, heartbeat both run lease and school lock, derive and audit candidates, ask the Supervisor at decision nodes, execute one typed action, validate the outcome, compare-and-swap cursor, and resume from the latest incomplete action after restart. Do not call `AgentWorkResult.next_phase`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest tests/unit/agent_graph tests/integration/agent_graph/test_worker.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_graph backend/app/agent_runtime backend/tests
git commit -m "feat: run lease-safe controlled agent graph"
```

## Task 6: Evidence manifests and phase-scoped MCP tools

**Files:**

- Create: `backend/app/agent_graph/evidence.py`
- Create: `backend/app/agent_graph/tools.py`
- Modify: `backend/app/ai/mcp/agent_gateway.py`
- Modify: `backend/app/ai/mcp/agent_authorization.py`
- Test: `backend/tests/unit/agent_graph/test_evidence.py`
- Test: `backend/tests/integration/agent_graph/test_tools.py`

**Interfaces:**

- Produces `EvidenceManifestService.issue()` and `.validate_reference()`.
- Produces `GraphPhaseToolGateway.call(tool_name, arguments, context)`.
- Every result is projected from manifest members and every call is audit-recorded.

- [ ] **Step 1: Add failing membership and capability tests**

```python
async def test_tool_rejects_resource_outside_manifest() -> None:
    with pytest.raises(UnsafeToolCall, match="evidence membership"):
        await gateway.call("read_work_item", {"resource_id": "foreign"}, context)

async def test_analysis_phase_cannot_request_connector_write() -> None:
    with pytest.raises(UnsafeToolCall, match="phase capability"):
        await gateway.call("request_operation_execution", {"operation_id": operation_id}, analysis_context)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/unit/agent_graph/test_evidence.py tests/integration/agent_graph/test_tools.py -q`

Expected: FAIL because manifest and graph gateway are absent.

- [ ] **Step 3: Implement manifest issuance and minimal tools**

Issue canonical hashes over tenant/task/run/node/action/snapshot pair/target version/resource IDs/evidence refs/tokens. Implement only the named tools from the design, with typed arguments, task-scoped repository lookups, phase capability checks and sanitized audit payload hashes.

- [ ] **Step 4: Run security verification**

Run: `cd backend && .venv/bin/pytest tests/unit/agent_graph/test_evidence.py tests/integration/agent_graph/test_tools.py tests/unit/ai/test_agent_tool_authorization.py tests/integration/ai/test_mcp_tools.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_graph backend/app/ai/mcp backend/tests
git commit -m "feat: bind graph agents to evidence tools"
```

## Task 7: Real inspection, normalization and analysis sub-agents

**Files:**

- Create: `backend/app/ai/graph_subagents.py`
- Create: `backend/app/agent_graph/analysis_executors.py`
- Modify: `backend/app/ai/skills/inspect-external-data-source/SKILL.md`
- Modify: `backend/app/ai/skills/normalize-organization-data-batch/SKILL.md`
- Modify: `backend/app/ai/skills/reconcile-entity-batch/SKILL.md`
- Modify: `backend/app/ai/skills/generate-governance-solutions/SKILL.md`
- Test: `backend/tests/integration/agent_graph/test_real_subagents.py`
- Test: `backend/tests/integration/agent_graph/test_analysis_path.py`

**Interfaces:**

- Produces typed executors for `inspect_sources`, `normalize_input_batches` and `analyze_actionable_batches`.
- Every invocation records `execution_mode="skill_model"`, Skill/schema versions, manifest ID and model provenance.
- No model failure silently substitutes a deterministic Handler output.

- [ ] **Step 1: Add failing milestone-one exit tests**

```python
async def test_required_actions_use_real_skill_model_output(session) -> None:
    for action_id in (
        "inspect_sources",
        "normalize_input_batches",
        "analyze_actionable_batches",
    ):
        invocation = await execute_scripted_graph_action(action_id)
        assert invocation.execution_mode == "skill_model"
        assert invocation.skill_version
        assert invocation.evidence_manifest_id
        assert invocation.model_request_id

async def test_model_exhaustion_does_not_delegate_to_legacy_handler(session) -> None:
    result = await execute_failing_analysis_action(attempts=4)
    assert result.status == "blocked_model_error"
    assert not await has_invocation_mode(result.run_id, "legacy_delegate")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/integration/agent_graph/test_real_subagents.py tests/integration/agent_graph/test_analysis_path.py -q`

Expected: FAIL because required graph action executors are absent.

- [ ] **Step 3: Implement the true Skill model path**

Inspection reads bounded connector metadata/pages through tools. Normalization processes at most 50 records per model batch and submits structured rows/marks. Analysis reads complete `PairedRecordEvidenceV1`, submits only actionable findings, and requires Chinese category, analysis and proposal. Reuse deterministic services only to build indexes, validate schemas, enforce claims and persist validated model output.

- [ ] **Step 4: Run exit-gate and privacy tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph/test_real_subagents.py tests/integration/agent_graph/test_analysis_path.py tests/unit/ai/test_agent_phone_privacy.py tests/unit/ai/test_agent_skill_content.py -q
```

Expected: PASS with no `legacy_delegate` invocation on normal graph paths.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai backend/app/agent_graph backend/tests
git commit -m "feat: run real ingestion and analysis subagents"
```

## Task 8: Human gates, governance, reports and rollback graph actions

**Files:**

- Create: `backend/app/agent_graph/governance_executors.py`
- Create: `backend/app/agent_graph/report_executors.py`
- Create: `backend/app/agent_graph/rollback_executors.py`
- Modify: `backend/app/governance/agent_governance.py`
- Modify: `backend/app/executions/agent_service.py`
- Modify: `backend/app/agent_reporting/service.py`
- Test: `backend/tests/integration/agent_graph/test_human_gates.py`
- Test: `backend/tests/e2e/test_agent_graph_governance.py`
- Test: `backend/tests/integration/agent_graph/test_rollback.py`

**Interfaces:**

- Human gates freeze exact members, versions and hashes.
- Model-selected execution uses only persisted ready operation IDs.
- Reports and rollback plans consume immutable verified facts.

- [ ] **Step 1: Add failing approval, partial-execution and rollback tests**

```python
async def test_phone_risk_group_waits_once_for_homogeneous_batch() -> None:
    gate = await run_until_human_gate(student_phone_findings(50))
    assert gate.kind == "high_risk_approval"
    assert len(gate.member_ids) == 50

async def test_execution_continues_independent_operation_after_failure() -> None:
    result = await execute_graph_plan(failing_operation="update-a")
    assert result.outcome("update-a") == "failed"
    assert result.outcome("create-independent") == "verified_success"

async def test_rollback_is_an_independent_locked_graph_run() -> None:
    rollback = await confirm_graph_rollback(original_task_id)
    assert rollback.workflow_version == "agent-graph-v1"
    assert rollback.run_kind == "rollback"
    assert rollback.report_id != original_report_id
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/integration/agent_graph/test_human_gates.py tests/e2e/test_agent_graph_governance.py tests/integration/agent_graph/test_rollback.py -q`

Expected: FAIL because graph governance/report/rollback executors are absent.

- [ ] **Step 3: Implement guarded executors**

Reuse risk, proposal compilation, preflight, execution, verification and fact reporting behind graph actions. The model may explain or select server-issued IDs but cannot construct writes. Termination drains the current atomic unit, preserves verified mutations, creates a facts-only report and releases the lock only after report persistence.

- [ ] **Step 4: Run focused and legacy regression tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph tests/e2e/test_agent_graph_governance.py tests/e2e/test_governance_execution.py tests/integration/agent_reporting/test_agent_reporting_and_rollback.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_graph backend/app/governance backend/app/executions backend/app/agent_reporting backend/tests
git commit -m "feat: complete governed agent graph lifecycle"
```

## Task 9: Graph API, events and human interaction

**Files:**

- Create: `backend/app/schemas/agent_graph_api.py`
- Modify: `backend/app/api/routes/agent.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/api/test_agent_graph_api.py`

**Interfaces:**

- Produces graph summary, cursor event, progress, gate submission, replan confirmation and termination endpoints.
- Responses contain business labels and sanitized fields, never raw prompts, graph hashes, phone values or internal paths.

- [ ] **Step 1: Add failing API contract tests**

```python
async def test_graph_progress_returns_business_labels(client) -> None:
    response = await client.get(f"/api/agent/tasks/{task_id}/graph")
    assert response.json()["business_stage"] == "agent_analysis"
    assert "current_action_zh" in response.json()
    assert "prompt" not in response.text

async def test_client_tenant_override_is_rejected(client) -> None:
    response = await client.post(
        f"/api/agent/tasks/{task_id}/gates/{gate_id}/decision",
        json={"decision": "approve", "tenant_id": "other-school"},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/integration/api/test_agent_graph_api.py -q`

Expected: FAIL because graph API contracts are absent.

- [ ] **Step 3: Implement typed endpoints**

Route by persisted workflow version, derive tenant from `OperatorContext`, enforce exact gate/cursor versions, return stable error codes, and preserve all `new-agent-v1` response behavior.

- [ ] **Step 4: Run API tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest tests/integration/api/test_agent_graph_api.py tests/integration/api/test_agent_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/app/schemas backend/app/main.py backend/tests/integration/api
git commit -m "feat: expose controlled graph task APIs"
```

## Task 10: Frontend graph progress and approval experience

**Files:**

- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/task-create/TaskCreatePage.tsx`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.tsx`
- Modify: `frontend/src/data/taskHistory.ts`
- Test: `frontend/src/api/agent.test.ts`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`
- Test: `frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx`

**Interfaces:**

- Both entry points create the same graph workflow when enabled.
- Active tasks restore from backend history after navigation or refresh.
- Normal input stays disabled during execution except typed conflict clarification.

- [ ] **Step 1: Add failing frontend tests**

```tsx
it("restores an active graph task from backend history", async () => {
  render(<ConversationCreatePage />);
  expect(await screen.findByText("正在检查第三方数据")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "终止任务" })).toBeEnabled();
});

it("renders one approval card for a homogeneous risk group", async () => {
  render(<AgentTaskDetailPage />);
  expect(await screen.findAllByRole("button", { name: "同意" })).toHaveLength(1);
});
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd frontend && npm test -- --run src/api/agent.test.ts src/features/task-create/ConversationCreatePage.test.tsx src/features/task-detail/AgentTaskDetailPage.test.tsx`

Expected: FAIL because graph response rendering is absent.

- [ ] **Step 3: Implement graph-aware UI**

Poll cursor events, map internal nodes to four business stages, render flowing progress, grouped approval and conflict-confirmation cards, preserve task history navigation, and keep legacy rendering for old workflow versions.

- [ ] **Step 4: Run frontend verification**

Run:

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat: render controlled agent graph progress"
```

## Task 11: Compatibility, security and release gates

**Files:**

- Modify: `backend/app/agent_runtime/README.md`
- Modify: `README.md`
- Modify: `dev.py`
- Create: `backend/tests/e2e/test_agent_graph_lifecycle.py`
- Create: `frontend/e2e/agent-graph.spec.ts`

**Interfaces:**

- Development launcher starts API and the worker mode that can route both Agent workflow versions.
- Graph feature flags remain default-off.
- Final acceptance rejects any normal graph invocation with `execution_mode="legacy_delegate"`.

- [ ] **Step 1: Add failing full-lifecycle and release assertions**

```python
async def test_graph_lifecycle_has_no_legacy_delegation(session) -> None:
    task = await run_synthetic_agent_graph_lifecycle()
    assert await task.completed()
    assert await invocation_modes(task.run_id) == {"skill_model", "deterministic_guarded"}
```

Add Playwright coverage for conversation start, navigation away/back, progress, grouped approval, completion, report and independent rollback.

- [ ] **Step 2: Run new end-to-end tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/e2e/test_agent_graph_lifecycle.py -q`

Expected: FAIL until all lifecycle routing and launcher integration is complete.

- [ ] **Step 3: Complete docs, launcher and compatibility routing**

Document exact flags, startup commands, school-lock behavior, model-error recovery and graph audit diagnostics. Ensure `legacy-v1` never enters either Agent worker and `new-agent-v1` never enters the graph worker.

- [ ] **Step 4: Run all delivery gates**

Run:

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
cd ../frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
npm run test:e2e
```

Expected: every command exits zero; tests use synthetic data and do not require live model credentials.

- [ ] **Step 5: Inspect final scope and commit**

Run: `git status --short && git diff --check && git log --oneline --decorate -12`

Confirm only planned files are changed, no `.env`, credentials, generated data, `.serena` memory or worktree artifacts are tracked.

```bash
git add README.md dev.py backend frontend
git commit -m "chore: finish controlled agent graph rollout"
```

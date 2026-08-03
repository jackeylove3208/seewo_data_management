# Retired workflow cleanup implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `agent-graph-v1` the only creatable and executable workflow, remove UI and backend code exclusive to `legacy-v1` or `new-agent-v1`, and retain historical data as read-only records.

**Architecture:** Seal retired creation and mutation entry points first, then remove their frontend routes and fixed worker. Delete backend modules only after the retained application, Graph worker, migration, model-metadata, and archived-history roots no longer import them. Treat Graph workflow, graph, ingestion, execution, mapping, adapter, Skill, evidence, and checkpoint versions as separate dimensions; every nested version reachable from `agent-graph-v1` remains supported.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, pytest, Ruff, mypy, React 19, TypeScript 5.8, TanStack Query, Vitest, Playwright, ESLint, Vite, OpenSpec.

## Global Constraints

- Preserve all `agent-graph-v1` behavior and all nested versions reachable from it.
- Preserve `agent-sync-graph-v1`, `agent-sync-graph-v2`, `agent-rollback-graph-v1`, `source-ingestion-v2`, `source-ingestion-v3`, and `deterministic-execution-v2`.
- Do not delete or rewrite historical task, run, event, checkpoint, report, execution, or audit rows.
- Keep every Alembic revision and every SQLAlchemy model needed to load existing databases.
- Historical `legacy-v1` and `new-agent-v1` tasks are read-only and cannot be advanced, retried, terminated, approved, executed, deleted through a retired endpoint, or used to start rollback.
- A file is deletable only after retained-root import and reference scans prove it unreachable.
- Use whole-file deletion or mechanical edits for the cleanup exceeding 500 lines.

---

### Task 1: Freeze the Graph-only product contract

**Files:**
- Modify: `openspec/specs/reconciliation-workflow-orchestration/spec.md`
- Modify: `backend/tests/unit/core/test_config.py`
- Modify: `backend/tests/integration/api/test_agent_api.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/api/routes/agent.py`
- Modify: `backend/app/agent_runtime/task_service.py`
- Modify: `backend/app/agent_runtime/service.py`
- Modify: `backend/app/schemas/agent_api.py`

**Interfaces:**
- Consumes: `Settings.new_task_workflow_version`, Agent task creation endpoints, `AgentTaskService.create`, and `AgentSupervisorService.start`.
- Produces: only `workflow_version="agent-graph-v1"` for new tasks while preserving nested Graph version selection.

- [ ] **Step 1: Change the OpenSpec requirement before production code**

Replace the historical-routing requirement with an explicit Graph-only creation and archived-history requirement:

```markdown
### Requirement: Route all new tasks to the controlled Graph workflow
The system SHALL create and execute new reconciliation tasks only as `agent-graph-v1` while retaining nested graph, ingestion, execution, mapping, Adapter, Skill, evidence, and checkpoint versions independently.

#### Scenario: Retired workflow task remains archived
- **WHEN** a stored task has `workflow_version=legacy-v1` or `workflow_version=new-agent-v1`
- **THEN** the system exposes only archived read-only metadata and rejects every workflow mutation without claiming work or acquiring a school lock
```

- [ ] **Step 2: Write failing Graph-only configuration and API tests**

Replace fallback assertions in `test_config.py` with:

```python
def test_new_task_workflow_version_is_always_graph() -> None:
    assert Settings(_env_file=None).new_task_workflow_version == "agent-graph-v1"
    assert Settings(new_agent_enabled=True, _env_file=None).new_task_workflow_version == "agent-graph-v1"
```

Change `test_manual_csv_task_uses_agent_runtime_and_exposes_persisted_events` to expect:

```python
assert task["workflow_version"] == "agent-graph-v1"
```

Add an API characterization that enables `new_agent_enabled` but disables `agent_graph_enabled` and expects HTTP 503 with `detail.code == "agent_graph_disabled"` before a task row is created.

- [ ] **Step 3: Run the tests and verify the expected failure**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/core/test_config.py tests/integration/api/test_agent_api.py -q
```

Expected: failures show the old fallback values and the missing Graph-enabled guard.

- [ ] **Step 4: Implement Graph-only creation without changing nested contracts**

Make the settings property unconditional:

```python
@property
def new_task_workflow_version(self) -> str:
    return "agent-graph-v1"
```

Require both rollout flags at the Agent HTTP boundary:

```python
def _require_enabled(request: Request) -> None:
    settings = request.app.state.settings
    if not settings.new_agent_enabled:
        raise HTTPException(503, detail=_error("new_agent_disabled", "New Agent workflow is disabled"))
    if not settings.agent_graph_enabled:
        raise HTTPException(503, detail=_error("agent_graph_disabled", "Agent graph workflow is disabled"))
```

Set new task records explicitly to `agent-graph-v1`, restrict `AgentSupervisorService.start` to that workflow, and narrow `AgentTaskResponse.workflow_version` to `Literal["agent-graph-v1"]`. Do not alter the existing `source-ingestion-v2/v3`, deterministic execution, or graph-version selection expressions.

- [ ] **Step 5: Verify Task 1**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/core/test_config.py tests/integration/api/test_agent_api.py tests/integration/agent_graph -q
.venv/bin/ruff check app/core/config.py app/api/routes/agent.py app/agent_runtime/task_service.py app/agent_runtime/service.py app/schemas/agent_api.py tests/unit/core/test_config.py tests/integration/api/test_agent_api.py
.venv/bin/mypy app
cd ..
openspec validate --all --strict --no-interactive
```

Expected: all commands exit zero and Graph v1/v2/v3 contract tests remain collected.

### Task 2: Remove retired frontend creation and workflow workbench

**Files:**
- Create: `frontend/src/features/task-detail/ArchivedTaskDetailPage.tsx`
- Modify: `frontend/src/features/task-detail/TaskDetailPage.tsx`
- Modify: `frontend/src/features/task-detail/TaskDetailPage.test.tsx`
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/app/App.test.tsx`
- Modify: `frontend/src/features/tasks/TaskListPage.tsx`
- Modify: `frontend/src/features/tasks/TaskListPage.test.tsx`
- Modify: `frontend/src/features/tasks/useTaskDeletion.tsx`
- Modify: `frontend/src/features/tasks/useTaskDeletion.test.tsx`
- Modify: `frontend/src/api/ingestion.ts`
- Modify: `frontend/src/styles/pageThemeCoverage.test.ts`
- Delete: `frontend/src/features/task-create/TaskCreatePage.tsx`
- Delete: `frontend/src/features/task-create/TaskCreatePage.test.tsx`
- Delete: `frontend/src/features/differences/DifferenceCategoryPage.tsx`
- Delete: `frontend/src/features/differences/DifferenceCategoryPage.test.tsx`
- Delete: `frontend/src/features/executions/ExecutionHistoryPage.tsx`
- Delete: `frontend/src/features/executions/ExecutionDetailPage.tsx`
- Delete: `frontend/src/features/executions/ExecutionDetailPage.test.tsx`
- Delete: `frontend/src/features/analysis/AnalysisModal.tsx`
- Delete: `frontend/src/features/analysis/AnalysisModal.test.tsx`
- Delete: `frontend/src/features/analysis/BatchAnalysisModal.tsx`
- Delete: `frontend/src/features/analysis/BatchAnalysisModal.test.tsx`
- Delete: `frontend/src/features/analysis/localization.ts`
- Delete: `frontend/src/features/task-detail/MatchingRecoveryPanel.tsx`
- Delete: `frontend/src/features/task-detail/MatchingRecoveryPanel.test.tsx`
- Delete: `frontend/src/features/workflow/useAnalysisJob.ts`
- Delete: `frontend/src/features/workflow/useAnalysisJob.test.tsx`
- Delete: `frontend/src/features/workflow/useReconciliationWorkflow.ts`
- Delete: `frontend/src/features/workflow/useReconciliationWorkflow.test.tsx`
- Delete: `frontend/src/features/workflow/useRematchingJob.ts`
- Delete: `frontend/src/features/workflow/useRematchingJob.test.tsx`
- Delete: `frontend/src/api/reconciliation.ts`
- Delete: `frontend/src/api/reconciliation.test.ts`
- Delete: `frontend/src/api/reporting.ts`

**Interfaces:**
- Consumes: task history metadata, `agentApi.task`, and `AgentTaskDetailPage`.
- Produces: conversation-only creation, unchanged Graph detail, and a mutation-free archived task view.

- [ ] **Step 1: Write failing route and archived-view tests**

Update `App.test.tsx` to assert the task-list primary action opens `/conversations/new` and `/tasks/new` redirects to `/tasks`. Add a `TaskDetailPage` case for a stored `new-agent-v1` task:

```tsx
saveStoredTask({ ...historyTask, workflowVersion: "new-agent-v1" });
render(<Routes><Route path="/tasks/:taskId" element={<TaskDetailPage />} /></Routes>, { wrapper });
expect(await screen.findByRole("heading", { name: "历史任务已归档" })).toBeInTheDocument();
expect(screen.queryByRole("button", { name: /重试|终止|审批|回滚/ })).not.toBeInTheDocument();
```

- [ ] **Step 2: Run the focused frontend tests and verify failure**

Run:

```bash
cd frontend
npm test -- --run src/app/App.test.tsx src/features/tasks/TaskListPage.test.tsx src/features/task-detail/TaskDetailPage.test.tsx
```

Expected: failures reference `/tasks/new` and the absent archived view.

- [ ] **Step 3: Implement the retained route tree**

The archived component accepts task history metadata and renders only title, workflow version, created time, terminal/history status, and this fixed message:

```tsx
<Alert
  type="info"
  showIcon
  message="历史任务已归档"
  description="该任务来自已停用的工作流，仅保留审计信息，不能继续执行或回滚。"
/>
```

Make `TaskDetailPage` route `agent-graph-v1` responses to the unchanged `AgentTaskDetailPage` and retired versions to `ArchivedTaskDetailPage`. Remove the classic workflow body. Route the task-list primary action to `/conversations/new`; remove `/tasks/new`, difference, and legacy execution routes from `App.tsx`. Make deletion call only `agentApi.deleteTask`.

- [ ] **Step 4: Delete the retired frontend dependency island**

Delete the files listed above, reduce `ingestion.ts` to the health request used by `ConnectionStatus`, and remove theme-coverage imports for deleted pages. Run:

```bash
rg -n 'TaskCreatePage|DifferenceCategoryPage|ExecutionHistoryPage|ExecutionDetailPage|AnalysisModal|BatchAnalysisModal|MatchingRecoveryPanel|useAnalysisJob|useReconciliationWorkflow|useRematchingJob|api/reconciliation|api/reporting' frontend/src
```

Expected: no matches.

- [ ] **Step 5: Verify Task 2**

Run:

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: all commands exit zero.

### Task 3: Remove retired HTTP APIs

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/integration/test_health.py`
- Delete: `backend/app/api/routes/analyses.py`
- Delete: `backend/app/api/routes/analysis_jobs.py`
- Delete: `backend/app/api/routes/differences.py`
- Delete: `backend/app/api/routes/execution_batches.py`
- Delete: `backend/app/api/routes/execution_records.py`
- Delete: `backend/app/api/routes/proposals.py`
- Delete: `backend/app/api/routes/reconciliation_tasks.py`
- Delete: `backend/app/api/routes/rematching_jobs.py`
- Delete: `backend/app/api/routes/reports.py`
- Delete: `backend/app/api/routes/restores.py`
- Delete: `backend/app/api/routes/uploads.py`
- Delete: the matching route-level test files under `backend/tests/integration/api/` for those deleted routers.

**Interfaces:**
- Consumes: FastAPI router registration.
- Produces: health, Agent/Graph, and API-connector HTTP surfaces only.

- [ ] **Step 1: Add an API-surface characterization**

Extend `test_health.py` to inspect `create_app(settings).routes`, asserting that `/api/agent/history` and `/api/connectors/providers` remain while `/api/reconciliation-tasks`, `/api/analysis-jobs/{job_id}`, and `/api/execution-records` are absent.

- [ ] **Step 2: Run the characterization and verify failure**

Run: `cd backend && .venv/bin/pytest tests/integration/test_health.py -q`

Expected: FAIL because retired routes remain registered.

- [ ] **Step 3: Remove retired router registration and files**

Keep only:

```python
app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(agent_router)
app.include_router(api_connectors.router)
app.include_router(api_connectors.external_identity_router)
```

Delete route modules and tests that exclusively exercise them. Do not delete services, schemas, repositories, or models in this task.

- [ ] **Step 4: Verify Task 3**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/test_health.py tests/integration/api/test_agent_api.py tests/integration/api/test_agent_graph_api.py tests/integration/api/test_api_connectors.py -q
.venv/bin/ruff check app/main.py tests/integration/test_health.py
.venv/bin/mypy app
```

Expected: all commands exit zero.

### Task 4: Stop fixed `new-agent-v1` execution

**Files:**
- Modify: `backend/app/agent_runtime/__main__.py`
- Modify: `backend/app/agent_runtime/worker.py`
- Modify: `backend/app/ai/worker.py`
- Modify: `backend/tests/unit/test_dev_launcher.py`
- Modify: `frontend/scripts/dev.mjs`
- Modify: `frontend/scripts/dev.test.mjs`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Delete: `backend/app/agent_runtime/csv_analysis_handlers.py`
- Delete: `backend/app/agent_runtime/csv_analysis_worker.py`
- Delete: `backend/tests/integration/agent_runtime/test_csv_analysis_worker.py`
- Delete: `backend/tests/integration/agent_runtime/test_worker.py`

**Interfaces:**
- Consumes: `python -m app.agent_runtime`.
- Produces: Graph worker plus connector credential maintenance, without a fixed phase worker or legacy analysis worker.

- [ ] **Step 1: Write launcher tests for one worker family**

Change launcher expectations to:

```javascript
args: ["-m", "app.agent_runtime"]
```

Add a backend launcher assertion that source inspection of `agent_runtime.__main__` contains `AgentGraphWorker` and does not contain `CsvAnalysisHandlerFactory` or `fixed_worker`.

- [ ] **Step 2: Run launcher tests and verify failure**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/test_dev_launcher.py -q
cd ../frontend
npm test -- --run scripts/dev.test.mjs
```

Expected: failures show the old worker entry and fixed-worker construction.

- [ ] **Step 3: Remove fixed-worker construction**

Remove `CsvAnalysisHandlerFactory`, `AgentWorker`, and `fixed_worker` from `agent_runtime.__main__`; initialize `workers` with only current maintenance workers and the Graph worker. Retain `AgentWorkContext` in `agent_runtime.worker` because `ProductionGraphActionExecutor` consumes it, but delete the unreferenced `AgentWorker` implementation after its tests are removed. Retain `WorkerRunner` and `run_worker_loop` in `ai.worker`, deleting the retired `AnalysisWorker` implementation and its exclusive imports.

- [ ] **Step 4: Update development entry points**

Make `frontend/scripts/dev.mjs`, `AGENTS.md`, and README use `app.agent_runtime`. Keep `dev.py` unchanged except for comments made stale by this cleanup.

- [ ] **Step 5: Verify Task 4**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/test_dev_launcher.py tests/integration/agent_graph/test_worker.py -q
.venv/bin/ruff check app/agent_runtime app/ai/worker.py tests/unit/test_dev_launcher.py
.venv/bin/mypy app
cd ../frontend
npm test -- --run scripts/dev.test.mjs
```

Expected: all commands exit zero and `rg -n 'new-agent-v1' backend/app/agent_runtime/__main__.py frontend/scripts/dev.mjs README.md AGENTS.md` returns no matches.

### Task 5: Remove unreachable retired backend islands

**Files:**
- Delete when the retained-root scan is empty: `backend/app/workflow/`
- Delete when the retained-root scan is empty: `backend/app/matching/`
- Delete when the retained-root scan is empty: `backend/app/differences/`
- Delete retired-only modules under `backend/app/ai/`, `backend/app/executions/`, `backend/app/governance/`, `backend/app/reports/`, `backend/app/restores/`, `backend/app/repositories/`, and `backend/app/schemas/` only when their importer set is also being deleted.
- Delete mirrored tests whose production subject was deleted.
- Preserve: all `backend/alembic/versions/*.py`
- Preserve: all model modules loaded by `app.models.Base.metadata`
- Preserve: `backend/app/agent_runtime/csv_governance_handlers.py`
- Preserve: `backend/app/agent_runtime/csv_rollback_handlers.py`
- Preserve: `backend/app/agent_runtime/sql_governance_handlers.py`
- Preserve: `backend/app/agent_runtime/sql_rollback_handlers.py`
- Preserve: all current Graph nested-version implementations.

**Interfaces:**
- Consumes: the retained root set defined in the design.
- Produces: no production modules reachable only from removed workflows.

- [ ] **Step 1: Capture the retained import graph before deletion**

Run reference scans from the repository root:

```bash
rg -n '^from app\.|^import app\.' backend/app/agent_graph backend/app/agent_runtime backend/app/agent_reporting backend/app/api/routes/agent.py backend/app/api_connectors backend/app/ingestion
rg -n 'source-ingestion-v2|source-ingestion-v3|deterministic-execution-v2|agent-sync-graph-v1|agent-sync-graph-v2|agent-rollback-graph-v1' backend/app backend/tests
```

Save the output in the task log, not the repository. Every preserved-version match must still resolve after deletion.

- [ ] **Step 2: Delete one dependency island at a time**

For each candidate package, remove its API/worker/test roots, run `rg` for imports of every module basename and qualified path, then delete the whole unreachable package or individual leaf modules with `apply_patch`. Never delete a module with a retained production importer.

- [ ] **Step 3: Run focused tests after each island**

After each deletion batch run the nearest retained Graph suite, followed by:

```bash
cd backend
.venv/bin/ruff check .
.venv/bin/mypy app
```

Expected: both commands exit zero before proceeding to the next island.

- [ ] **Step 4: Verify no retired entry points remain**

Run:

```bash
rg -n 'workflow_version="legacy-v1"|workflow_versions=frozenset\(\{"new-agent-v1"\}\)|CsvAnalysisHandlerFactory|class AgentWorker|/tasks/new' backend/app frontend/src frontend/scripts
```

Expected: no matches. References in Alembic, archived-history labels, explicit mutation rejection, and historical fixtures are allowed and reviewed manually.

### Task 6: Document the read-only deployment audit

**Files:**
- Modify: `backend/README.md`

**Interfaces:**
- Consumes: existing PostgreSQL task, run, and lock tables.
- Produces: an operator-run read-only query; no new runtime code and no data mutation.

- [ ] **Step 1: Add the exact read-only audit query**

Document this query for the deployment database:

```sql
SELECT
  task.id AS task_id,
  task.workflow_version,
  run.id AS run_id,
  run.status AS run_status,
  lock.id AS active_lock_id
FROM reconciliation_tasks AS task
LEFT JOIN agent_runs AS run ON run.task_id = task.id
LEFT JOIN school_task_locks AS lock
  ON lock.owner_task_id = task.id AND lock.active IS TRUE
WHERE task.workflow_version IN ('legacy-v1', 'new-agent-v1')
  AND (
    run.status IN ('pending', 'running', 'waiting_human', 'terminating')
    OR lock.id IS NOT NULL
  )
ORDER BY task.id, run.id;
```

State that any returned row blocks deployment until an operator resolves it separately. Do not
provide or run an automatic update/delete query as part of this cleanup.

- [ ] **Step 2: Verify documentation and schema names**

Run:

```bash
rg -n '__tablename__ = "(reconciliation_tasks|agent_runs|school_task_locks)"' backend/app/models
rg -n "legacy-v1|new-agent-v1|SELECT|school_task_locks" backend/README.md
git diff --check -- backend/README.md
```

Expected: all three table names match the models and the documentation diff has no whitespace errors.

### Task 7: Run full gates and report the cleanup

**Files:**
- Verify: all changed files.

**Interfaces:**
- Consumes: Tasks 1-6.
- Produces: verified Graph-only repository and before/after cleanup metrics.

- [ ] **Step 1: Recompute tracked code metrics**

Count the same extensions and categories used in the baseline. Record production files/lines, test files/lines, migrations, and deleted paths. Do not count `.venv`, `node_modules`, build output, or caches.

- [ ] **Step 2: Run the full backend gate**

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

Expected: all configured tests pass with only documented external-service skips.

- [ ] **Step 3: Run the clean PostgreSQL migration smoke test**

```bash
cd backend
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

Expected: PASS; if Docker/PostgreSQL is unavailable, report that external blocker without claiming the gate passed.

- [ ] **Step 4: Run the full frontend gate**

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
npm run test:e2e
```

Expected: all commands exit zero; report unavailable Chrome/browser infrastructure explicitly.

- [ ] **Step 5: Run contract and diff checks**

```bash
openspec validate --all --strict --no-interactive
git diff --check
git status --short
rg -n 'source-ingestion-v2|source-ingestion-v3|deterministic-execution-v2|agent-sync-graph-v1|agent-sync-graph-v2|agent-rollback-graph-v1' backend/app backend/tests
```

Expected: OpenSpec and diff checks pass, only intended files differ, and all protected nested versions still have production implementations and tests.

- [ ] **Step 6: Review the final diff against the design**

Confirm there are no data deletions, migration deletions, Graph behavior changes, new mutation paths for archived workflows, or unrelated refactors. Report every retained old-looking module together with its current Graph importer.

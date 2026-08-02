# Dead Code and Worker Entry Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove code proven to have no production consumer while preserving existing worker startup behavior.

**Architecture:** Preserve the separate `app.ai.worker` legacy-analysis and `app.agent_runtime` Agent entry points because they have different configuration and queue responsibilities. Delete only symbols excluded from the API, worker, Alembic, and frontend entry graphs; adapt tests to active production interfaces before removing redundant wrappers.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, pytest, Ruff, mypy, Node.js, TypeScript, Vitest, ESLint, Vite.

## Global Constraints

- Preserve all HTTP request and response contracts.
- Do not change database models, migrations, workflow versions, or reconciliation behavior.
- Retain `AgentRetryableTargetError` and `RetryableConnectorError` as extension contracts.
- Do not remove or redirect `app.ai.worker`; `npm run dev` depends on its default-compatible startup and legacy analysis queue consumer.
- Make no unrelated formatting, naming, or helper consolidation changes.

---

### Task 1: Verify and preserve development worker compatibility

**Files:**
- Verify: `frontend/scripts/dev.test.mjs`
- Verify: `frontend/scripts/dev.mjs`
- Verify: `AGENTS.md`

**Interfaces:**
- Consumes: the default-compatible executable module `app.ai.worker`.
- Produces: an unchanged development plan whose worker argv ends with `("-m", "app.ai.worker")`.

- [ ] **Step 1: Confirm the launcher contract uses the compatible worker**

Keep every worker assertion and expected argv in `frontend/scripts/dev.test.mjs` as:

```javascript
["-m", "app.ai.worker"]
```

- [ ] **Step 2: Run the launcher compatibility test**

Run: `cd frontend && npm test -- --run scripts/dev.test.mjs`

Expected: PASS with `frontend/scripts/dev.mjs` returning `app.ai.worker`.

- [ ] **Step 3: Verify the two worker entry points remain distinct**

Confirm the frontend development plan remains:

```javascript
worker: {
  command: backendPython,
  args: ["-m", "app.ai.worker"],
  environment: backendEnvironment,
},
```

Confirm `AGENTS.md` uses `app.ai.worker`, while `dev.py` uses `app.agent_runtime` with explicit Agent rollout overrides.

- [ ] **Step 4: Run the focused launcher test and checks**

Run:

```bash
cd frontend
npm test -- --run scripts/dev.test.mjs
npm run lint
npm run typecheck
```

Expected: launcher tests, ESLint, and TypeScript checks pass.

- [ ] **Step 5: Record that no launcher change is part of this cleanup**

```bash
git diff --exit-code -- AGENTS.md frontend/scripts/dev.mjs frontend/scripts/dev.test.mjs
```

### Task 2: Remove obsolete gateway and unused declarations

**Files:**
- Delete: `backend/app/ai/mcp/agent_gateway.py`
- Delete: `backend/tests/integration/ai/test_agent_phase_gateway.py`
- Modify: `backend/app/connectors/base.py`
- Modify: `backend/app/schemas/analysis_jobs.py`
- Modify: `backend/tests/unit/schemas/test_analysis_job_contracts.py`
- Modify: `backend/app/matching/conflict_resolver.py`
- Modify: `backend/app/matching/exact_matcher.py`
- Modify: `backend/app/models/executions.py`

**Interfaces:**
- Consumes: active `GraphPhaseToolGateway`, header-bound analysis-job creation, `DEFAULT_KEY_POLICIES`, and `*Record` ORM names.
- Produces: the same runtime behavior without unreachable compatibility definitions.

- [ ] **Step 1: Capture the active replacement and API behavior before deletion**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/integration/agent_graph/test_tools.py \
  tests/integration/api/test_analysis_jobs.py \
  tests/unit/matching/test_conflict_resolver.py \
  tests/unit/matching/test_exact_matcher.py -q
```

Expected: all selected production-path tests pass before cleanup.

- [ ] **Step 2: Remove tests that validate only unreachable contracts**

Delete `tests/integration/ai/test_agent_phase_gateway.py`. In `tests/unit/schemas/test_analysis_job_contracts.py`, remove the `AnalysisJobCreateRequest` import and the test that constructs it with a blank idempotency key; endpoint validation remains covered through the header-bound API tests.

- [ ] **Step 3: Delete the unreachable code**

Delete `app/ai/mcp/agent_gateway.py`. Remove these declarations without changing neighboring active code:

```python
ConnectorNotConfigured
AnalysisJobCreateRequest
CARDINALITY_STATUSES
STABLE_KEYS
GovernancePlan
ExecutionBatch
ExecutionOperation
OperationAttempt
TargetVersion
ExecutionAuditEvent
```

Also remove imports made unused by those declarations, including `field_validator` from `schemas/analysis_jobs.py` and `KeyPolicy` if no active annotation uses it.

- [ ] **Step 4: Verify active behavior and absence of removed references**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/integration/agent_graph/test_tools.py \
  tests/integration/api/test_analysis_jobs.py \
  tests/unit/schemas/test_analysis_job_contracts.py \
  tests/unit/matching/test_conflict_resolver.py \
  tests/unit/matching/test_exact_matcher.py -q
.venv/bin/ruff check .
.venv/bin/mypy app
```

Then run from the repository root:

```bash
rg -n 'AgentPhaseToolGateway|ConnectorNotConfigured|AnalysisJobCreateRequest|CARDINALITY_STATUSES|STABLE_KEYS' backend/app backend/tests
```

Expected: tests and static checks pass; the reference scan returns no matches.

- [ ] **Step 5: Commit obsolete-code removal**

```bash
git add -A backend/app backend/tests
git commit -m "refactor: remove obsolete agent contracts"
```

### Task 3: Remove redundant production wrappers

**Files:**
- Modify: `backend/app/agent_runtime/database_mapping.py`
- Modify: `backend/tests/integration/agent_runtime/test_sql_governance_worker.py`
- Modify: `backend/app/reconciliation/agent_identity.py`
- Modify: `backend/tests/unit/reconciliation/test_agent_identity.py`

**Interfaces:**
- Consumes: `load_frozen_database_mapping_context(...)->FrozenDatabaseMapping` and the production `_record_postings` helper.
- Produces: one database-mapping loader interface and one identity-posting implementation.

- [ ] **Step 1: Move the database mapping test onto the production interface**

Replace the test import of `load_frozen_database_mapping` with `load_frozen_database_mapping_context`. Replace the wrapper assertion with:

```python
context = await load_frozen_database_mapping_context(
    session,
    task_id=task.id,
    run_id=run.id,
    role="target",
)
assert context.mapping == expected_mapping
```

- [ ] **Step 2: Run the focused database mapping test**

Run: `cd backend && .venv/bin/pytest tests/integration/agent_runtime/test_sql_governance_worker.py -q`

Expected: PASS while both loader functions still exist.

- [ ] **Step 3: Remove the redundant loaders and test-only identity helper**

Delete `load_frozen_database_mapping` from `database_mapping.py`. Remove the `identity_postings` import and its direct unit assertion from `test_agent_identity.py`, then delete `identity_postings` from `agent_identity.py`; retain `_record_postings`, which is used by the identity index builder.

- [ ] **Step 4: Verify mapping and identity production paths**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/integration/agent_runtime/test_sql_governance_worker.py \
  tests/integration/agent_runtime/test_agent_identity_handler.py \
  tests/unit/reconciliation/test_agent_identity.py -q
.venv/bin/ruff check .
.venv/bin/mypy app
```

Expected: focused tests and static checks pass without the redundant functions.

- [ ] **Step 5: Commit wrapper cleanup**

```bash
git add backend/app/agent_runtime/database_mapping.py \
  backend/tests/integration/agent_runtime/test_sql_governance_worker.py \
  backend/app/reconciliation/agent_identity.py \
  backend/tests/unit/reconciliation/test_agent_identity.py
git commit -m "refactor: remove redundant agent helpers"
```

### Task 4: Verify the complete cleanup

**Files:**
- Verify only: all changed files.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: evidence that behavior, types, builds, and repository references remain valid.

- [ ] **Step 1: Run the full backend quality gate**

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy app
```

Expected: all configured backend tests pass, except documented external-service skips; Ruff and mypy exit zero.

- [ ] **Step 2: Run the full frontend quality gate**

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: all frontend tests and checks pass.

- [ ] **Step 3: Run final structural checks**

```bash
rg -n 'app\.ai\.worker' AGENTS.md frontend/scripts/dev.mjs frontend/scripts/dev.test.mjs
rg -n 'app\.agent_runtime' dev.py README.md backend/README.md
rg -n 'AgentPhaseToolGateway|ConnectorNotConfigured|AnalysisJobCreateRequest|CARDINALITY_STATUSES|STABLE_KEYS' backend/app backend/tests
git diff --check
git status --short
```

Expected: each launcher references its intentional worker family; no removed-symbol references; no whitespace errors; only intended files differ from the design/plan base.

- [ ] **Step 4: Review the final diff**

Confirm that `AgentRetryableTargetError`, `RetryableConnectorError`, `app.ai.worker`, workflow versions, API routes, database models, and migrations remain present and behaviorally unchanged.

# Dead code cleanup implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove production code, framework dependencies, tests, exports, and assets that have no path from a supported runtime entry point.

**Architecture:** Treat `app.main`, `app.agent_runtime`, `app.ai.worker`, `dev.py`, and `frontend/src/main.tsx` as runtime roots. Delete only candidates confirmed by the import graph, repository-wide symbol search, and a second dead-code analyzer; keep migration history and all three persisted workflow versions.

**Tech stack:** Python 3.12, FastAPI, SQLAlchemy, pytest, React 19, TypeScript, Vitest, Ruff, mypy, Knip.

## Global constraints

- Preserve all current API, worker, CLI, and UI behavior.
- Do not modify or revert the user's pre-existing dirty files.
- Delete tests only when their sole subject is the deleted, runtime-unreachable implementation.
- Keep Alembic migrations even when they describe historical features.
- Run focused tests after each batch and the repository quality gates at the end.
- Do not commit or stage changes unless the user explicitly requests it.

---

### Task 1: Remove unused framework dependencies

**Files:**
- Modify: `backend/app/ai/mcp/server.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements-ci.txt`
- Modify: `backend/tests/integration/ai/test_mcp_tools.py`
- Modify: `backend/tests/integration/ai/test_agent_phase_gateway.py`

**Interfaces:**
- Keeps: `MCPToolGateway`, `ToolResult`, and the server-owned authorization gateways used by production.
- Removes: the unstarted FastMCP transport factories and the zero-use `tenacity` dependency.

- [ ] **Step 1: Remove transport-only tests**

Delete `test_fastmcp_registers_only_gateway_tools` and the equivalent agent FastMCP registration test. Keep gateway authorization and scope tests unchanged.

- [ ] **Step 2: Remove the unused transport adapter**

Delete `create_fastmcp_server`, `create_agent_fastmcp_server`, their context-provider aliases, and imports used only by those functions.

- [ ] **Step 3: Remove dependencies**

Remove these exact dependency entries:

```toml
"mcp>=1.8,<2",
"tenacity>=9,<10",
```

Remove the corresponding `mcp==...` and `tenacity==...` lines from `backend/requirements-ci.txt`.

- [ ] **Step 4: Verify**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/ai/test_mcp_tools.py tests/integration/ai/test_agent_phase_gateway.py -q
.venv/bin/ruff check app/ai/mcp/server.py tests/integration/ai
.venv/bin/mypy app
```

Expected: all selected tests pass, Ruff passes, and mypy reports no issues.

### Task 2: Delete runtime-unreachable implementation chains

**Files:**
- Delete: `backend/app/ai/rematching_agent.py`
- Delete: `backend/app/ai/rematching_policy.py`
- Delete: `backend/app/ai/rematching_service.py`
- Delete: `backend/app/ai/rematching_worker.py`
- Delete: `backend/app/connectors/database.py`
- Delete: `backend/app/connectors/registry.py`
- Delete: `backend/app/connectors/seewo_api.py`
- Delete: `backend/app/connectors/third_party_api.py`
- Delete: `backend/app/governance/eligibility.py`
- Delete: `backend/tests/integration/ai/test_rematching_worker.py`
- Delete: `backend/tests/unit/ai/test_rematching_agent.py`
- Delete: `backend/tests/unit/ai/test_rematching_policy.py`
- Modify: `backend/tests/contract/test_connectors.py`
- Modify: `backend/tests/integration/ai/test_analysis_security.py`
- Modify: `backend/tests/integration/ai/test_analysis_service.py`
- Modify: `backend/tests/integration/api/test_analyses.py`

**Interfaces:**
- Keeps: active rematching API/models/repositories, deterministic matching, configured SQL connector runtime, CSV connectors, and API-reported execution eligibility.
- Removes: an AI rematching worker never started by any supported launcher, placeholder connector wrappers used only by tests, and an eligibility oracle used only as a test helper.

- [ ] **Step 1: Remove tests whose only subject is unreachable code**

Delete the three AI rematching test files. In `test_connectors.py`, keep protocol and CSV connector coverage while deleting registry/placeholder connector tests. Remove direct `ExecutionEligibility` assertions and the stale-version test that invokes only that unused helper.

- [ ] **Step 2: Delete the unreachable modules**

Delete the nine production modules listed above. Do not remove rematching migrations, API routes, models, repositories, or the active SQL runtime.

- [ ] **Step 3: Verify**

Run:

```bash
cd backend
.venv/bin/pytest tests/contract/test_connectors.py tests/integration/ai/test_analysis_security.py tests/integration/ai/test_analysis_service.py tests/integration/api/test_analyses.py -q
.venv/bin/ruff check app tests
.venv/bin/mypy app
```

Expected: all selected tests pass, with no imports of the deleted modules.

### Task 3: Remove test-only production adapters and duplicate services

**Files:**
- Modify: `backend/app/connectors/configured.py`
- Modify: `backend/app/executions/agent_service.py`
- Modify: `backend/app/agent_graph/supervisor.py`
- Delete: `backend/tests/integration/agent_graph/test_supervisor.py`
- Create: `backend/tests/fixtures/connector_store.py`
- Modify: connector and execution tests importing `InMemoryConnectorStore`
- Modify: `backend/tests/contract/test_configured_connectors.py`
- Modify: `backend/tests/unit/executions/test_agent_execution.py`

**Interfaces:**
- Keeps: `ConfiguredApiConnector`, the active `SqlAlchemyConnectorStore`, SQL execution paths, `AgentExecutionService`, CSV target adapter, and `build_supervisor_context`.
- Removes: the uncomposed HTTP connector store, static credential resolver, test in-memory store from the production package, configured-agent adapter unused by any executor, and the duplicate supervisor persistence service bypassed by `AgentGraphWorker`.
- Produces: `tests.fixtures.connector_store.InMemoryConnectorStore` for tests that still need a deterministic connector store.

- [ ] **Step 1: Relocate the deterministic test store**

Move `InMemoryConnectorStore` unchanged into `backend/tests/fixtures/connector_store.py` and update test imports. The production module must no longer expose a test double.

- [ ] **Step 2: Remove uncomposed connector implementations**

Delete `CredentialResolver`, `StaticCredentialResolver`, and `HttpJsonConnectorStore`. Delete only the HTTP-store tests; retain configured SQL connector tests.

- [ ] **Step 3: Remove the unused execution adapter**

Delete `ConfiguredConnectorAgentTarget` and `_ConfiguredConnectorSession`, plus tests dedicated to that adapter. Keep generic `AgentExecutionService` tests.

- [ ] **Step 4: Remove the duplicate supervisor service**

Delete `SupervisorDecisionService` and its dedicated integration test. Keep `build_supervisor_context`, which is called by `AgentGraphWorker`.

- [ ] **Step 5: Verify**

Run:

```bash
cd backend
.venv/bin/pytest tests/contract/test_configured_connectors.py tests/unit/ingestion/test_agent_database_adapter.py tests/unit/agent_runtime/test_sql_rollback_handlers.py tests/integration/agent_runtime/test_sql_governance_worker.py tests/integration/agent_graph/test_production_runtime.py tests/unit/executions/test_agent_execution.py tests/unit/agent_graph/test_contracts.py -q
.venv/bin/ruff check app tests
.venv/bin/mypy app
```

Expected: all selected tests pass and production imports contain no test store.

### Task 4: Remove isolated dead symbols and frontend residue

**Files:**
- Modify: `backend/app/ai/agent_durable_analysis.py`
- Modify: `backend/app/agent_runtime/retry.py`
- Modify: `backend/app/ai/analysis_policy.py`
- Modify: `backend/app/agent_graph/analysis_executors.py`
- Modify: `backend/app/matching/vector_index.py`
- Modify: `backend/app/schemas/analysis_jobs.py`
- Modify: `backend/app/schemas/reporting.py`
- Modify: `backend/app/schemas/agent_reconciliation.py`
- Modify: `backend/app/schemas/rematching.py`
- Modify: focused tests for those symbols
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/data/taskHistory.ts`
- Modify: frontend imports using `apiUrl`
- Delete: `frontend/apple-preview.svg`
- Delete: `package-lock.json`

**Interfaces:**
- Keeps: all symbols referenced by runtime code.
- Removes: test-only retry/partition/similarity helpers, four unreferenced response schemas, test-only enums/contracts, a duplicate URL alias, an unnecessary exported constant, and two orphan files.

- [ ] **Step 1: Delete backend leaf symbols**

Delete only symbols with no production caller:

```text
analyze_with_four_total_attempts
run_model_with_retries
validate_analysis_action
partition_bounded_resources
local_similarity_features
LOCAL_SIMILARITY_FIELDS
AnalysisJobEvent
AnalysisJobControlResponse
ReportJobResponse
RestoreRequestResponse
IdentityKeyKind
WorkItemKind
WorkItemState
KeyPolicyEvidence
RematchingJobProgress
```

Delete or narrow tests that exist only for those symbols.

- [ ] **Step 2: Simplify frontend exports**

Replace `apiUrl(...)` calls with `resolveApiUrl(...)`, delete the alias, and make `UNKNOWN_TARGET_SOURCE_KEY` module-private.

- [ ] **Step 3: Delete orphan files**

Delete the empty root npm lockfile and the unreferenced historical SVG preview.

- [ ] **Step 4: Verify**

Run:

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy app
cd ../frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: backend and frontend quality gates pass with no new warnings.

### Task 5: Final repository verification

**Files:**
- Inspect: all changed files

**Interfaces:**
- Consumes: the four independently verified cleanup batches.
- Produces: a reviewable dead-code-only diff.

- [ ] **Step 1: Re-run static reachability checks**

Run the Python import-graph audit and Knip again. Confirm no removed symbol or module remains referenced.

- [ ] **Step 2: Run repository quality gates**

Run:

```bash
cd backend
PYTHONPATH="$PWD" .venv/bin/pytest --import-mode=importlib
.venv/bin/ruff check .
.venv/bin/mypy app
cd ../frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
cd ..
openspec validate --all --strict --no-interactive
```

Expected: 0 failures. The PostgreSQL-only migration and real-model tests may remain skipped when their opt-in environment variables are absent.

- [ ] **Step 3: Review the diff**

Confirm `git diff --check` passes, all pre-existing dirty paths remain preserved, and every deletion has either zero references or only deleted dedicated tests.

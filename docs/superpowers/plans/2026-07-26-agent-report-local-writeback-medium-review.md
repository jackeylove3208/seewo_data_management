# Agent report, local CSV writeback, and medium-risk review implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task by task.

**Goal:** Make `agent-graph-v1` render an evidence-rich Apple-style report, atomically publish verified changes to an explicitly authorized local Seewo CSV, and pause for auditable per-item medium- and high-risk decisions.

**Architecture:** Keep the server as the authority for local-path discovery, risk classification, graph cursors, frozen finding membership, operation compilation, file publication, and report facts. Extend the existing local-source façade with write capabilities, copy mutable local targets into managed immutable version storage before governance, and publish only the latest verified managed version through a conflict-aware atomic publisher. Reuse graph human-gate JSON for the exact member decision partition while widening persisted approval groups to medium and high risk. The React workbench only presents server facts and submits decisions tied to the frozen gate.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, pytest, React 18, TypeScript, TanStack Query, Ant Design, Vitest/Testing Library.

## Global constraints

- Work only in an isolated `codex/agent-local-writeback-risk-review` worktree.
- Preserve all unrelated dirty files in the main checkout.
- Follow red-green-refactor for every behavior change.
- Never accept an absolute local path or client-provided writable flag.
- Never write the authoritative third-party file.
- Never expose an unmasked student phone in an API response, report fact, event, or test snapshot.
- Browser uploads remain managed copies; direct original-file writeback is local-source-only.
- A failed or conflicted publication must not claim successful completion or silently release the school lock.
- Keep the existing `agent-graph-v1` guards, graph cursor checks, content hashes, idempotency, audit, and rollback facts.

---

## Task 1: Add trusted local write-root configuration and discovery contract

**Files:**

- Modify: `backend/app/core/config.py`
- Modify: `backend/app/local_sources/service.py`
- Modify: `backend/app/schemas/agent_api.py`
- Modify: `backend/app/api/routes/agent.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/unit/core/test_config.py`
- Test: `backend/tests/unit/local_sources/test_service.py`
- Test: `backend/tests/integration/api/test_agent_api.py`

**Step 1: Write failing configuration and access tests**

Add tests proving:

- `agent_local_write_roots` is canonicalized;
- every write root must be equal to or nested under a read root;
- a discovered source includes `writable_as_target`;
- traversal, absolute references, symlinks, blocked paths, non-CSV files, and paths outside write roots cannot be resolved for writing;
- authoritative-role resolution is rejected even when the file lies below a write root;
- `GET /api/agent/local-sources` returns only safe relative references and server-computed target writability.

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/core/test_config.py tests/unit/local_sources/test_service.py tests/integration/api/test_agent_api.py -q
```

Expected: FAIL because write roots, capability fields, and the endpoint do not exist.

**Step 2: Implement the minimum trusted capability**

- Add `Settings.agent_local_write_roots`.
- Validate containment after both root lists are canonicalized.
- Extend `LocalSourceSummary` with `writable_as_target: bool`.
- Add a role-aware `describe_target_for_write(source_ref)` that resolves the same server-owned reference, rechecks the real path and symlink status, and requires write-root containment.
- Add a read-only API route returning the discovered list.
- Keep conversation context based on the same discovery list.

**Step 3: Run the focused tests**

Run the command from Step 1 and require PASS.

**Step 4: Commit**

```bash
git add backend/app/core/config.py backend/app/local_sources/service.py backend/app/schemas/agent_api.py backend/app/api/routes/agent.py backend/.env.example backend/tests/unit/core/test_config.py backend/tests/unit/local_sources/test_service.py backend/tests/integration/api/test_agent_api.py
git commit -m "feat: authorize local agent write targets"
```

## Task 2: Preserve an immutable initial target and atomically publish verified versions

**Files:**

- Create: `backend/app/local_sources/publisher.py`
- Modify: `backend/app/agent_runtime/csv_governance_handlers.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Modify: `backend/app/agent_runtime/csv_rollback_handlers.py`
- Test: `backend/tests/unit/local_sources/test_publisher.py`
- Test: `backend/tests/integration/agent_runtime/test_csv_governance_worker.py`
- Test: `backend/tests/integration/agent_graph/test_production_runtime.py`
- Test: `backend/tests/unit/agent_runtime/test_csv_rollback_handlers.py`

**Step 1: Write failing publisher and lifecycle tests**

Cover:

- first local-target version is a managed copy under `export_root`, not the mutable destination;
- successful publication uses a same-directory temporary file, `fsync`, `os.replace`, and hash readback;
- a changed destination hash returns a stable conflict without replacement;
- repeating the same target-version/hash publication is idempotent;
- no succeeded mutation means no replacement;
- termination after a verified mutation publishes before its report;
- rollback publishes its verified restore version through the same authority checks.

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/local_sources/test_publisher.py tests/integration/agent_runtime/test_csv_governance_worker.py tests/integration/agent_graph/test_production_runtime.py tests/unit/agent_runtime/test_csv_rollback_handlers.py -q
```

Expected: FAIL because the publisher and managed local initial copy do not exist.

**Step 2: Implement a filesystem-pure atomic publisher**

In `publisher.py`, add immutable input/result contracts and:

- resolve only a `LocalSourceService`-validated writable target;
- compare destination hash against the last observed/published task hash;
- short-circuit exact idempotent retries;
- copy the managed version to a temporary file in the destination directory;
- flush and `fsync` the file, atomically replace, `fsync` the directory, then hash-readback;
- clean up temporary files on failure;
- return safe references and hashes without leaking an absolute path.

**Step 3: Integrate immutable version creation and checkpoints**

- When `_finding_inputs` creates the initial version for a local target, copy it into managed export storage first.
- Persist the original observed target hash and later publication outcome in a durable Agent checkpoint/event keyed by task/run and target version.
- Before terminal, termination, or rollback report generation, publish the latest verified successful managed version when one exists.
- Treat hash conflict, revoked authorization, missing destination, symlink substitution, replace failure, and readback mismatch as stable blocking errors; do not report target update success.

**Step 4: Run the focused tests**

Run the command from Step 1 and require PASS.

**Step 5: Commit**

```bash
git add backend/app/local_sources/publisher.py backend/app/agent_runtime/csv_governance_handlers.py backend/app/agent_graph/production_executor.py backend/app/agent_runtime/csv_rollback_handlers.py backend/tests/unit/local_sources/test_publisher.py backend/tests/integration/agent_runtime/test_csv_governance_worker.py backend/tests/integration/agent_graph/test_production_runtime.py backend/tests/unit/agent_runtime/test_csv_rollback_handlers.py
git commit -m "feat: atomically publish local agent targets"
```

## Task 3: Enforce deletion for target-extra findings

**Files:**

- Modify: `backend/app/agent_graph/analysis_tools.py`
- Modify: `backend/app/ai/agent_analysis.py`
- Modify: `backend/app/ai/skills/reconcile-entity-batch/SKILL.md`
- Modify: `backend/app/ai/skills/generate-governance-solutions/SKILL.md`
- Modify: `backend/app/agent_graph/analysis_executors.py`
- Test: `backend/tests/unit/ai/test_agent_skill_content.py`
- Test: `backend/tests/integration/agent_graph/test_real_subagents.py`
- Test: `backend/tests/unit/agent_graph/test_contracts.py`

**Step 1: Write failing contract tests**

Prove that:

- the Skill contracts say `target_extra` recommends only `delete`;
- server tool contracts expose only `delete`;
- a model output that returns `retain`/`skip`/`update` for `target_extra` is rejected before persistence or operation compilation;
- valid delete output remains accepted and server-classified high risk.

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/ai/test_agent_skill_content.py tests/integration/agent_graph/test_real_subagents.py tests/unit/agent_graph/test_contracts.py -q
```

Expected: FAIL because `target_extra` currently permits `retain`.

**Step 2: Align prompt, tool, and server validation**

Remove `retain` from all target-extra operation sets and add an explicit kind/operation compatibility validation at the structured model-output boundary.

**Step 3: Run focused tests and commit**

```bash
cd backend
.venv/bin/pytest tests/unit/ai/test_agent_skill_content.py tests/integration/agent_graph/test_real_subagents.py tests/unit/agent_graph/test_contracts.py -q
cd ..
git add backend/app/agent_graph/analysis_tools.py backend/app/ai/agent_analysis.py backend/app/ai/skills/reconcile-entity-batch/SKILL.md backend/app/ai/skills/generate-governance-solutions/SKILL.md backend/app/agent_graph/analysis_executors.py backend/tests/unit/ai/test_agent_skill_content.py backend/tests/integration/agent_graph/test_real_subagents.py backend/tests/unit/agent_graph/test_contracts.py
git commit -m "fix: require deletion proposals for target extras"
```

## Task 4: Persist per-item medium- and high-risk review decisions

**Files:**

- Create: `backend/alembic/versions/0031_agent_reviewable_approval_groups.py`
- Modify: `backend/app/models/agent_analysis.py`
- Modify: `backend/app/governance/agent_governance.py`
- Modify: `backend/app/repositories/agent_governance.py`
- Modify: `backend/app/agent_runtime/csv_governance_handlers.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Modify: `backend/app/schemas/agent_graph_api.py`
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/unit/governance/test_agent_governance.py`
- Test: `backend/tests/integration/agent_graph/test_human_gates.py`
- Test: `backend/tests/integration/api/test_agent_graph_api.py`
- Test: `backend/tests/e2e/test_agent_graph_lifecycle.py`

**Step 1: Write failing domain and API tests**

Cover:

- grouping includes medium and high items, at most 50 per card, while low-risk retain/skip remains ungated;
- delete and student-phone update are high, ordinary create/update are medium;
- decision requests must provide an exact, duplicate-free partition of frozen member IDs;
- cursor and membership hash mismatch remain conflicts;
- mixed approve/reject persists exact per-member outcomes in `AgentHumanGateRecord.decision`;
- compilation consumes approved finding IDs and excludes rejected findings;
- dependencies on rejected findings become blocked report facts;
- all rejected produces a valid no-change plan outcome and proceeds to report instead of raising;
- exact retry is idempotent and a changed retry conflicts.

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/governance/test_agent_governance.py tests/integration/agent_graph/test_human_gates.py tests/integration/api/test_agent_graph_api.py tests/e2e/test_agent_graph_lifecycle.py -q
```

Expected: FAIL because only high-risk group-level decisions are supported.

**Step 2: Widen persisted review groups**

- Replace the database/model `risk = 'high'` constraint with `risk IN ('medium', 'high')`.
- Generalize grouping to reviewable findings while retaining deterministic IDs and membership hashes.
- Persist the server risk on each group.

**Step 3: Extend the gate decision contract**

- Add `approved_finding_ids`, `rejected_finding_ids`, `graph_cursor`, and `membership_hash` to review-gate requests.
- Validate the exact frozen partition under the row lock.
- Persist per-member outcomes in the human-gate decision JSON.
- Mark the associated approval group as decided only after a valid complete partition.
- Preserve the existing simple decision shape for termination, rollback, and clarification gates.

**Step 4: Compile only approved members**

- Refactor `compile_agent_plan` to receive approved and rejected finding IDs.
- Exclude rejected findings without treating them as errors.
- Record dependency-blocked findings.
- Let an empty approved set advance through execution to a completed no-change report.

**Step 5: Run focused tests and migration smoke**

```bash
cd backend
.venv/bin/pytest tests/unit/governance/test_agent_governance.py tests/integration/agent_graph/test_human_gates.py tests/integration/api/test_agent_graph_api.py tests/e2e/test_agent_graph_lifecycle.py -q
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add backend/alembic/versions/0031_agent_reviewable_approval_groups.py backend/app/models/agent_analysis.py backend/app/governance/agent_governance.py backend/app/repositories/agent_governance.py backend/app/agent_runtime/csv_governance_handlers.py backend/app/agent_graph/production_executor.py backend/app/schemas/agent_graph_api.py backend/app/api/routes/agent.py backend/tests/unit/governance/test_agent_governance.py backend/tests/integration/agent_graph/test_human_gates.py backend/tests/integration/api/test_agent_graph_api.py backend/tests/e2e/test_agent_graph_lifecycle.py
git commit -m "feat: review agent operations per finding"
```

## Task 5: Enrich authoritative report facts safely

**Files:**

- Modify: `backend/app/agent_runtime/csv_governance_handlers.py`
- Modify: `backend/app/agent_reporting/service.py`
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/integration/agent_graph/test_reporting.py`
- Test: `backend/tests/integration/agent_reporting/test_agent_reporting_and_rollback.py`
- Test: `backend/tests/integration/api/test_agent_api.py`

**Step 1: Write failing report-fact tests**

Create synthetic information-center phone and target-extra student findings and assert the public report includes:

- safe entity identity and locator;
- Chinese analysis and solution;
- recommended operation;
- per-item operator decision;
- succeeded/rejected/blocked/failed status;
- changed field names and masked before/after values;
- local source reference and publication status;
- no raw student phone value anywhere in serialized facts/content.

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph/test_reporting.py tests/integration/agent_reporting/test_agent_reporting_and_rollback.py tests/integration/api/test_agent_api.py -q
```

Expected: FAIL because report facts currently keep only finding IDs, kinds, and categories.

**Step 2: Build joined safe finding facts**

Join findings to their work items, input records, solutions, frozen gate decisions, operations, and publication checkpoint. Add a dedicated masker for phone-like fields and keep execution facts server-authoritative. Generate a deterministic Chinese fallback narrative when model narrative is absent or malformed.

**Step 3: Run focused tests and commit**

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph/test_reporting.py tests/integration/agent_reporting/test_agent_reporting_and_rollback.py tests/integration/api/test_agent_api.py -q
cd ..
git add backend/app/agent_runtime/csv_governance_handlers.py backend/app/agent_reporting/service.py backend/app/api/routes/agent.py backend/tests/integration/agent_graph/test_reporting.py backend/tests/integration/agent_reporting/test_agent_reporting_and_rollback.py backend/tests/integration/api/test_agent_api.py
git commit -m "feat: expose safe agent report evidence"
```

## Task 6: Let the external-sync page select trusted local sources

**Files:**

- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-create/TaskCreatePage.tsx`
- Modify: `frontend/src/features/task-create/TaskCreatePage.test.tsx`

**Step 1: Write failing UI tests**

Assert:

- local source options come from the backend;
- third-party selection accepts a discovered read-only source;
- target selection enables only `writable_as_target` sources;
- the submitted task uses `{kind: "local", source_ref}` without an absolute path or client writable flag;
- browser CSV upload still uses `upload_id` and is labeled as managed-copy mode.

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/TaskCreatePage.test.tsx
```

Expected: FAIL because manual sync supports upload/API/database only.

**Step 2: Implement local-source selection**

Add the typed list API and render a clear “本地授权 CSV（可直接写回）” option alongside upload mode. Require server-declared target writability and show the writeback behavior before submission.

**Step 3: Run the focused test and commit**

```bash
cd frontend
npm test -- --run src/features/task-create/TaskCreatePage.test.tsx
cd ..
git add frontend/src/api/agent.ts frontend/src/features/task-create/TaskCreatePage.tsx frontend/src/features/task-create/TaskCreatePage.test.tsx
git commit -m "feat: select trusted local csv sources"
```

## Task 7: Build per-item review controls in the task detail page

**Files:**

- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.tsx`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx`
- Modify: `frontend/src/styles/apple.css`

**Step 1: Write failing interaction tests**

Prove:

- medium items initialize selected, high items initialize unresolved;
- per-item approve/reject toggles work independently;
- all-approve/all-reject shortcuts only affect the current frozen card;
- submission is disabled until every high-risk item is decided;
- mixed decisions submit exact IDs, gate cursor, and membership hash;
- successful persisted decisions render “已允许/已拒绝” per row;
- stale/conflict responses keep the card visible and reload server state.

Run:

```bash
cd frontend
npm test -- --run src/features/task-detail/AgentTaskDetailPage.test.tsx
```

Expected: FAIL because the current page submits one decision for the whole group.

**Step 2: Implement frozen per-item state**

- Extend gate types with risk, cursor, membership hash, and persisted member outcomes.
- Initialize state from each gate only; do not infer authority from UI defaults.
- Render medium and high labels, detailed operations, per-row decisions, bulk shortcuts, and “按当前选择继续”.
- Submit a complete exact partition to the API and refresh task, graph, and events.

**Step 3: Run the focused test and commit**

```bash
cd frontend
npm test -- --run src/features/task-detail/AgentTaskDetailPage.test.tsx
cd ..
git add frontend/src/api/agent.ts frontend/src/features/task-detail/AgentTaskDetailPage.tsx frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx frontend/src/styles/apple.css
git commit -m "feat: review agent findings individually"
```

## Task 8: Render the model narrative and evidence appendix in Apple style

**Files:**

- Modify: `frontend/src/features/reports/AgentReportPage.tsx`
- Create: `frontend/src/features/reports/AgentReportPage.test.tsx`
- Modify: `frontend/src/styles/apple.css`

**Step 1: Write failing presentation tests**

Assert:

- the page has the Apple theme and no default white report surfaces;
- `content.narrative.title_zh` and `summary_zh` appear before facts;
- terminal and publication states are translated;
- information-center and target-extra student facts appear with safe details;
- accepted, rejected, blocked, failed, and succeeded results are distinguishable;
- malformed/missing narrative falls back to Chinese server text while facts remain.

Run:

```bash
cd frontend
npm test -- --run src/features/reports/AgentReportPage.test.tsx
```

Expected: FAIL because the page ignores `content` and uses default Ant Design surfaces.

**Step 2: Implement the report hierarchy**

Use `apple-page`, a dark-glass narrative hero, compact metrics, publication status, actionable fact cards, governance result sections, and a clearly labeled “服务端事实附录（权威）”. Do not render raw object values or unmasked phones.

**Step 3: Run the focused test and commit**

```bash
cd frontend
npm test -- --run src/features/reports/AgentReportPage.test.tsx
cd ..
git add frontend/src/features/reports/AgentReportPage.tsx frontend/src/features/reports/AgentReportPage.test.tsx frontend/src/styles/apple.css
git commit -m "feat: redesign agent synchronization reports"
```

## Task 9: Run repository quality gates and perform focused cleanup

**Files:**

- Modify only files already in this plan when cleanup is necessary.

**Step 1: Run backend gates**

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

**Step 2: Run frontend gates**

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

**Step 3: Run migration smoke**

```bash
cd backend
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

**Step 4: Inspect the final diff**

Confirm:

- no `.env`, credentials, storage exports, local data, or unrelated files are included;
- public contracts contain no raw phone values or absolute paths;
- only code made obsolete by this change is removed;
- all documented requirements have a passing automated test.

**Step 5: Commit any verified cleanup**

```bash
git add <only-the-cleanup-files>
git commit -m "refactor: finalize local agent governance flow"
```


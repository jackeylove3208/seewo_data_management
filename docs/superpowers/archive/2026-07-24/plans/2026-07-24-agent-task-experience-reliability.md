# Agent task experience reliability implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Agent task deletion and termination failures, make model waits observable, and present the Agent workflow in a readable Apple-style Chinese interface.

**Architecture:** Keep persisted audit event identifiers stable, but add a frontend presentation adapter that converts them into Chinese timeline entries. Fix the backend data-integrity defects at their source, preserve the bounded four-attempt model policy, record each model attempt as an event, and treat `blocked_model_error` as a visible blocked state rather than active work.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Alembic, PostgreSQL/SQLite, pytest, React, TypeScript, TanStack Query, Vitest, CSS.

## Global constraints

- Work only in `codex/fix-agent-task-experience`; preserve uncommitted main-worktree OpenSpec changes.
- Keep raw event identifiers for audit compatibility.
- Model execution remains one initial attempt plus at most three retries.
- `blocked_model_error` only allows task termination.
- Student phone values remain tokenized at the model boundary.
- Use synthetic fixtures only.

---

### Task 1: Agent report and deletion integrity

**Files:**
- Modify: `backend/app/models/reporting.py`
- Modify: `backend/app/tasks/deletion_service.py`
- Test: `backend/tests/integration/agent_reporting/test_agent_reporting_and_rollback.py`
- Test: `backend/tests/integration/tasks/test_task_deletion.py`

**Interfaces:**
- Consumes: `AgentReportingService.generate(...)` and `TaskDeletionService.delete(...)`.
- Produces: reports whose ORM shape matches the migrated schema, and deletion that removes unexecuted Agent analysis records before deleting the run.

- [x] **Step 1: Write failing PostgreSQL-shape report test**

Assert that a newly generated `AgentReportRecord` has a non-null `updated_at`.

- [x] **Step 2: Run the report test and verify failure**

Run:

```bash
backend/.venv/bin/pytest backend/tests/integration/agent_reporting/test_agent_reporting_and_rollback.py -q
```

Expected: failure because `AgentReportRecord` does not map or populate `updated_at`.

- [x] **Step 3: Implement the report timestamp**

Add an explicit non-null `updated_at` mapped column with UTC defaults to `AgentReportRecord`, matching migration `0020_agent_reporting_history`.

- [x] **Step 4: Write failing deletion test with persisted Agent analysis records**

Seed an Agent run with input records, identity postings, claims, work items, model batches, attempts, and connector capabilities. Delete the task and assert all records are gone.

- [x] **Step 5: Run the deletion test and verify foreign-key failure**

Run:

```bash
backend/.venv/bin/pytest backend/tests/integration/tasks/test_task_deletion.py -q
```

Expected: failure when the service deletes `agent_runs` while analysis children still reference it.

- [x] **Step 6: Implement dependency-ordered deletion**

Delete leaf analysis rows before parents: solutions/dependencies/attempts/batch items/evidence/marks, followed by findings/clarifications/approvals/operations/plans/claims/postings/work items/batches/inputs/capabilities, then the Agent run.

- [x] **Step 7: Run both focused suites**

Expected: all report and deletion tests pass.

### Task 2: Observable bounded model attempts

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Modify: `backend/app/ai/agent_durable_analysis.py`
- Modify: `backend/app/agent_runtime/csv_analysis_worker.py`
- Test: `backend/tests/integration/agent_runtime/test_agent_durable_analysis.py`
- Test: `backend/tests/integration/agent_runtime/test_csv_analysis_worker.py`

**Interfaces:**
- Consumes: `Settings.llm_timeout_seconds`, `DurableAgentBatchAnalyzer.analyze_batch(...)`.
- Produces: `model_attempt_started`, `model_attempt_failed`, and existing exhaustion events with attempt counts and safe failure categories.

- [x] **Step 1: Write failing attempt-event tests**

Assert each attempt emits a start event, failed attempts emit a safe category, and exhaustion remains four total attempts.

- [x] **Step 2: Verify the tests fail for missing events**

Run the two focused runtime test modules.

- [x] **Step 3: Persist safe attempt progress**

Append events inside the same fenced batch transaction without storing raw provider exceptions. Categorize timeouts as `model_timeout` and all other validation/transport failures as bounded safe codes.

- [x] **Step 4: Increase the default model request timeout**

Change the default and example configuration from 20 to 60 seconds. Keep local `.env` secrets untouched.

- [x] **Step 5: Run focused runtime tests**

Expected: progress events and four-attempt behavior pass.

### Task 3: Chinese event presentation and blocked-state behavior

**Files:**
- Create: `frontend/src/features/agent-events/presentation.ts`
- Create: `frontend/src/features/agent-events/presentation.test.ts`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx`

**Interfaces:**
- Consumes: unchanged `AgentTaskEvent` payloads.
- Produces: `presentAgentEvent(event)` returning Chinese `title`, `description`, `tone`, and formatted time.

- [x] **Step 1: Write failing presentation adapter tests**

Cover run creation, lock acquisition, phase transitions, model attempt start/failure/exhaustion, approvals, report completion, termination, and an unknown-event fallback that never exposes the raw English identifier.

- [x] **Step 2: Verify adapter tests fail because the module is absent**

Run the new Vitest module.

- [x] **Step 3: Implement the adapter and timeline**

Keep raw event types out of visible text. Render semantic icons, Chinese titles, compact details, and localized timestamps.

- [x] **Step 4: Write failing blocked-state component tests**

Assert `blocked_model_error` displays “模型分析已暂停”, stops the running animation, keeps termination available, and disables normal chat input.

- [x] **Step 5: Implement blocked-state rendering**

Add `blocked_model_error` to the non-running state contract while preserving it as non-terminal for termination.

- [x] **Step 6: Run all affected frontend tests**

Expected: adapter, conversation, and task-detail suites pass.

### Task 4: Apple-style blue-black contrast

**Files:**
- Modify: `frontend/src/styles/apple.css`
- Modify: `frontend/src/styles/global.css`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`
- Test: `frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx`

**Interfaces:**
- Consumes: existing `.apple-page`, `.conversation-message.user`, `.conversation-card`, `.stage-track`, and `.stage` classes.
- Produces: separate blue-black surfaces for user messages and workflow phases, distinct from the purple-blue assistant surface.

- [x] **Step 1: Add semantic class assertions**

Assert user messages, progress cards, stage cards, and event timeline entries have stable semantic classes and accessible text.

- [x] **Step 2: Verify tests fail for missing semantic state classes**

Run both component suites.

- [x] **Step 3: Implement the visual tokens**

Use deep navy for user messages, a slightly lighter blue-black for workflow cards, readable pale text, restrained borders, and no white-on-white combinations.

- [x] **Step 4: Run frontend quality gates**

Run:

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: all commands exit zero.

### Task 5: Final backend and migration verification

**Files:**
- Verify only.

**Interfaces:**
- Consumes: the completed backend and frontend changes.
- Produces: fresh evidence that the original failures are fixed without regressions.

- [x] **Step 1: Run backend quality gates**

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

- [x] **Step 2: Run the clean PostgreSQL migration smoke test**

```bash
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

- [x] **Step 3: Review the worktree diff**

Confirm there are no credentials, generated outputs, unrelated OpenSpec edits, or destructive schema changes.

### Review hardening: deletion and model transaction boundaries

- [x] Block deletion from direct governance-operation facts, even before a report exists.
- [x] Lock Agent runs and block deletion while governance or restore execution is active.
- [x] Preserve SQLite append-only DELETE triggers with a transaction-scoped deletion guard.
- [x] Test deletion against the complete persisted Agent analysis dependency graph.
- [x] Commit each model-attempt start event before entering the long-running model call.
- [x] Retry only model/provider/output failures; propagate repository and fencing failures.
- [x] Release a model batch claim after infrastructure failure so the phase cannot skip it.
- [x] Keep the worker lease longer than the configured model timeout.
- [x] Re-run backend, PostgreSQL migration, and frontend quality gates.

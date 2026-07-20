# Reconciliation Stalled Run Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent an existing development database with an older `analysis_results` schema from leaving difference detection stuck in `running`, and make the failed state retryable and visible.

**Architecture:** Keep Alembic as the schema authority. Development startup must apply the pending migration against the configured SQLite database before serving requests, while production startup must not silently mutate schema. Workflow stage failures must be persisted as failed attempts and returned to the frontend so retry remains explicit.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, SQLite development database, Vitest/pytest.

## Global Constraints

- Preserve existing `backend/storage/dev.db` data; do not delete or recreate it.
- Do not send data to an LLM for this fix; the failure occurs before AI analysis.
- Add regression tests before implementation and verify the focused tests plus existing backend/frontend checks.
- Do not change governance behavior unrelated to schema migration or workflow failure recovery.

### Task 1: Upgrade Existing Development Schema

**Files:**
- Modify: `backend/alembic/env.py` to honor the configured development database URL.
- Modify: `frontend/scripts/dev.mjs` to run the pending Alembic upgrade before starting Uvicorn.
- Test: `backend/tests/integration/test_migrations.py` and `frontend/scripts/dev.test.mjs`.

- [ ] Add a migration/startup test using a SQLite database whose `analysis_results` table lacks `gateway_request_ids`; assert the upgrade adds the non-null JSON column with an empty-list default.
- [ ] Add a development-launcher test asserting migration runs before backend serving and uses the same `RECONCILIATION_DATABASE_URL` as Uvicorn.
- [ ] Run the focused tests and confirm they fail against the current implementation.
- [ ] Implement the smallest URL propagation and migration invocation that keeps normal Alembic migrations unchanged.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Persist Recoverable Workflow Failure

**Files:**
- Modify: `backend/app/workflow/service.py` and/or `backend/app/repositories/workflow.py` only where needed to handle stage database failures consistently.
- Modify: `frontend/src/features/task-detail/TaskDetailPage.tsx` only if the existing failed workflow response is not rendered correctly.
- Test: `backend/tests/integration/api/test_ingestion_api.py` or a focused workflow test, plus a frontend regression test if UI behavior changes.

- [ ] Add a failing regression test that makes difference detection hit the old-schema error and asserts the workflow response is `failed`, retryable when appropriate, and no longer reports `pending/running` forever.
- [ ] Run the test to verify the expected failure before production changes.
- [ ] Implement transaction-safe failure recording without hiding the original database error.
- [ ] Ensure retry starts a fresh stage attempt after the schema is upgraded and completes difference detection.
- [ ] Run focused backend and frontend tests, then the relevant full suites.

## Verification

Run from the repository root after implementation:

```bash
cd backend && .venv/bin/pytest -q tests/integration/test_migrations.py tests/integration/api/test_ingestion_api.py
cd ../frontend && npm test -- --run scripts/dev.test.mjs src/features/task-detail/TaskDetailPage.test.tsx
```

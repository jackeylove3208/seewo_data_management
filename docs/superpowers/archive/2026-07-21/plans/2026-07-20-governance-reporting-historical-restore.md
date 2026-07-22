# Governance Reporting and Historical Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate immutable AI-assisted HTML reports and restore the current target to any selected historical target version through one verified append-only compensation batch.

**Architecture:** Reporting snapshots execution facts, reuses the configured analysis model for structured narrative, and falls back deterministically. Restore planning resolves a version path from immutable target versions and verified operations, validates optional AI candidates against the deterministic plan, and reuses the ordinary execution service.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Jinja2, React, TypeScript, TanStack Query, Vitest, pytest.

## Global Constraints

- Original executions, target versions, reports, and restore requests are append-only.
- Actor identity comes from backend authentication context.
- AI never decides restore eligibility and never mutates the target.
- Every restore is high risk and confirmation binds the preview hash and current target version.
- Only synthetic organization data is used in tests.

---

### Task 1: Persist report and restore records

**Files:**
- Create: `backend/app/schemas/reporting.py`
- Create: `backend/app/models/reporting.py`
- Create: `backend/app/repositories/reporting.py`
- Create: `backend/alembic/versions/0012_reporting_historical_restore.py`
- Test: `backend/tests/integration/repositories/test_reporting.py`

**Interfaces:**
- Produces: `ExecutionFactBundle`, `GovernanceReportContent`, `ReportJobResponse`, `RestorePreview`, `RestoreRequestResponse`, and `ReportingRepository`.

- [x] Write failing repository tests for append-only versions, report idempotency, immutable restore links, and tenant scope.
- [x] Run `.venv/bin/pytest tests/integration/repositories/test_reporting.py -q` and verify missing-module failure.
- [x] Implement schemas, models, migration, repository, and immutable SQLAlchemy listeners.
- [x] Run the repository tests and migration test to green.

### Task 2: Collect facts and generate HTML reports

**Files:**
- Create: `backend/app/reports/facts.py`
- Create: `backend/app/reports/narrative.py`
- Create: `backend/app/reports/renderer.py`
- Create: `backend/app/reports/service.py`
- Create: `backend/app/reports/templates/governance-report.html.j2`
- Modify: `backend/app/ai/skills/generate-governance-report/SKILL.md`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/integration/reports/test_report_service.py`

**Interfaces:**
- Consumes: `ExecutionRecordService.get_detail`, `LLMProvider.complete_json`, and `ReportingRepository`.
- Produces: `ReportService.generate(execution_id, idempotency_key) -> GovernanceReportResponse`.

- [x] Write failing tests for eligible statuses, fixed facts, version increments, same-key idempotency, AI provenance, fallback, and HTML escaping.
- [x] Run the focused tests and verify expected failures.
- [x] Implement canonical fact hashing, tokenized structured AI narrative, deterministic fallback, renderer, and service.
- [x] Run focused tests to green.

### Task 3: Expose report APIs

**Files:**
- Create: `backend/app/api/routes/reports.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/api/test_reports.py`

**Interfaces:**
- Produces: create/list/detail/download endpoints under `/api/execution-records/{id}/reports` and `/api/reports/{id}`.

- [x] Write failing API tests for backend actor, idempotency, status, HTML response, tenant isolation, and ineligible execution state.
- [x] Run the API tests and verify route failures.
- [x] Implement route wiring and error mapping.
- [x] Run API tests to green.

### Task 4: Build deterministic and AI-assisted historical restore plans

**Files:**
- Create: `backend/app/restores/path.py`
- Create: `backend/app/restores/planner.py`
- Create: `backend/app/restores/advisor.py`
- Create: `backend/app/restores/service.py`
- Modify: `backend/app/ai/skills/assess-rollback-impact/SKILL.md`
- Test: `backend/tests/integration/restores/test_restore_planner.py`

**Interfaces:**
- Produces: `RestoreService.preview(current_version_id, target_version_id) -> RestorePreview` and `confirm(preview_hash, idempotency_key) -> ExecutionBatchConfirmation`.

- [x] Write failing tests for backward inverse order, forward replay, V3-to-V1 then V4-to-V2, stale current version, verification-failed operations, and AI mismatch/fallback.
- [x] Run focused restore tests and verify expected failures.
- [x] Implement semantic source resolution, version-path walking, inverse/replay operation construction, AI advisory validation, preview hashing, and compensation plan persistence.
- [x] Run restore tests to green.

### Task 5: Execute and expose historical restores

**Files:**
- Create: `backend/app/api/routes/restores.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/executions/executor.py`
- Test: `backend/tests/integration/api/test_restores.py`
- Test: `backend/tests/e2e/test_report_and_restore.py`

**Interfaces:**
- Produces: target-version timeline, restore preview/confirm endpoints, restore status, and output content-hash verification.

- [x] Write failing API and end-to-end tests for high-risk acknowledgement, stale preview, one-batch execution, failed compensation, repeated historical restore, and immutable originals.
- [x] Run tests and verify expected failures.
- [x] Implement routes, execution linkage, and selected-version content verification.
- [x] Run API and end-to-end tests to green.

### Task 6: Build report and restore workbench

**Files:**
- Create: `frontend/src/features/executions/ExecutionHistoryPage.tsx`
- Create: `frontend/src/features/executions/ExecutionDetailPage.tsx`
- Create: `frontend/src/features/executions/ExecutionDetailPage.test.tsx`
- Create: `frontend/src/api/reporting.ts`
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**
- Produces: execution history/detail, report generation/view/download, version timeline, restore preview/conflicts, high-risk confirmation, and execution link navigation.

- [x] Write failing component/API tests for report states, restore selection, conflicts, AI explanation fallback, acknowledgement, and confirmation.
- [x] Run `npm test -- --run src/features/executions/ExecutionDetailPage.test.tsx` and verify failures.
- [x] Implement typed API client, routes, operational views, and responsive styles.
- [x] Run frontend tests, lint, typecheck, and build to green.

### Task 7: Verify and update contracts

**Files:**
- Modify: `openspec/changes/demo/tasks.md`
- Test: all backend/frontend/OpenSpec verification commands.

- [x] Run `.venv/bin/pytest`, `.venv/bin/ruff check .`, and `.venv/bin/mypy app`.
- [x] Run `npm test -- --run`, `npm run lint`, `npm run typecheck`, and `npm run build`.
- [x] Run `openspec validate demo`.
- [x] Mark 7.2 and 11.1-11.8 complete only for verified delivered behavior.

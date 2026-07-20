# Optimize AI Analysis Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace request-bound batches of ten with recoverable per-item AI analysis, guarantee clear Chinese resolution paths, and add preview-first task-level adoption of safe recommendations.

**Architecture:** PostgreSQL stores analysis jobs and leased work items; a separate async worker claims one item with `SKIP LOCKED`, releases the transaction before calling the enterprise model, and commits each outcome independently. `analysis-v3` uses a discriminated union for executable, information-needed, and manual-only paths. FastAPI exposes job status/SSE, summaries, and idempotent batch preview/confirmation; React observes persisted progress and never writes source data directly.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, PostgreSQL/SQLite tests, pytest, React 19, TypeScript, TanStack Query, Ant Design, Vitest, Testing Library, Playwright.

## Global Constraints

- Preserve all pre-existing uncommitted reconciliation recovery changes and do not overwrite `backend/.env.example` credential-like values.
- Do not create Git commits; the user will commit after review.
- Every production behavior change follows RED -> GREEN -> focused regression verification.
- AI one-click handling creates only immutable `pending_execution` proposals; it never calls a target connector.
- High-risk, insufficient-evidence, identity-uncertain, or parent-uncertain items never become auto-executable.
- Every readable difference receives at least one Simplified Chinese resolution path; AI, API, and CSV are the only default Latin-term exceptions.

---

### Task 1: Analysis-v3 Contract and Chinese Policy

**Files:**
- Modify: `backend/app/schemas/governance.py`
- Modify: `backend/app/ai/analysis_policy.py`
- Modify: `backend/app/ai/prompting.py`
- Modify: `backend/app/ai/deterministic_analysis.py`
- Modify: `backend/app/ai/analysis_service.py`
- Modify: `backend/app/ai/skills/analyze-data-difference/SKILL.md`
- Modify: `backend/app/ai/skills/registry.py`
- Modify: `backend/app/repositories/analyses.py`
- Test: `backend/tests/unit/schemas/test_analysis_v3_contracts.py`
- Test: `backend/tests/unit/ai/test_analysis_v3_policy.py`
- Test: `backend/tests/integration/ai/test_analysis_service.py`

**Interfaces:**
- Produces: `CauseAnalysisV3`, `ResolutionPath`, `AutoExecutableResolution`, `NeedsInformationResolution`, `ManualResolution`, `CURRENT_ANALYSIS_VERSION = "analysis-v3"`.
- Consumes: existing `RecommendedAction`, `RiskLevel`, difference evidence, tokenization, and provenance.

- [ ] Write schema tests proving `solutions` requires 1..3 entries, one recommendation, and mode-specific payloads; run `.venv/bin/pytest tests/unit/schemas/test_analysis_v3_contracts.py -q` and confirm RED.
- [ ] Implement the discriminated Pydantic union and v3 `AnalysisResult` status rules; rerun and confirm GREEN.
- [ ] Write policy tests for Chinese readability, forbidden raw codes, invented values, high risk, evidence references, and safe manual fallback; run `.venv/bin/pytest tests/unit/ai/test_analysis_v3_policy.py -q` and confirm RED.
- [ ] Implement `validate_analysis_v3`, stable Chinese label maps, and safe deterministic fallbacks; rerun and confirm GREEN.
- [ ] Update Skill/prompt/agent output schema and add corrective validation feedback to the second model attempt.
- [ ] Extend repository conversion so v1 uses `CauseAnalysis`, v2 uses `CauseAnalysisV2`, and v3 uses `CauseAnalysisV3`; verify existing v1/v2 repository tests remain green.

### Task 2: Durable Job Models and Migration

**Files:**
- Create: `backend/app/models/analysis_jobs.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0010_durable_analysis_jobs.py`
- Create: `backend/app/schemas/analysis_jobs.py`
- Test: `backend/tests/unit/schemas/test_analysis_job_contracts.py`
- Modify: `backend/tests/integration/test_migrations.py`

**Interfaces:**
- Produces: `AnalysisJobRecord`, `AnalysisWorkItemRecord`, `AnalysisJobStatus`, `AnalysisWorkItemStatus`, API response/request schemas.
- Database uniqueness: `(tenant_id, task_id, idempotency_key)` and `(job_id, difference_id, difference_version)`.

- [ ] Add failing model/schema tests for counters, terminal states, leases, retry metadata, and localized fallback; verify RED.
- [ ] Implement focused SQLAlchemy models and Pydantic projections; verify GREEN.
- [ ] Add migration tests expecting both tables, indexes, reversibility, and head revision `0010_durable_analysis_jobs`; verify RED.
- [ ] Implement migration without altering existing `0006`-`0009` recovery code; verify migration tests GREEN.

### Task 3: Job Repository, Service, and Worker

**Files:**
- Create: `backend/app/repositories/analysis_jobs.py`
- Create: `backend/app/ai/job_service.py`
- Create: `backend/app/ai/worker.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/workflow/service.py`
- Test: `backend/tests/integration/repositories/test_analysis_jobs.py`
- Test: `backend/tests/integration/ai/test_analysis_worker.py`
- Modify: `backend/tests/integration/workflow/test_service.py`

**Interfaces:**
- Produces: `AnalysisJobRepository.create_or_get`, `claim_next`, `complete_item`, `schedule_retry`, `recover_expired_leases`; `AnalysisJobService.create_job`; `AnalysisWorker.run_once`.
- Worker ownership is a generated stable process ID; leases are UTC timestamps.

- [ ] Write repository tests for idempotent creation, one item per difference version, claim exclusivity, recovery, completion counters, cancellation, and retry subset; verify RED.
- [ ] Implement repository methods with short transactions and PostgreSQL `with_for_update(skip_locked=True)` plus SQLite-compatible test behavior; verify GREEN.
- [ ] Write worker tests proving model calls occur after claim commit, completed items survive later failure, transient errors back off, and exhausted errors yield Chinese manual fallback; verify RED.
- [ ] Implement `AnalysisJobService` and `AnalysisWorker`; verify GREEN.
- [ ] Update workflow tests so the analysis stage creates/reuses a job and returns without model calls; then update `ReconciliationWorkflowService` and verify workflow tests GREEN.

### Task 4: Job APIs, SSE, and Task Projection

**Files:**
- Create: `backend/app/api/routes/analysis_jobs.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/routes/reconciliation_tasks.py`
- Modify: `backend/app/schemas/workflow.py`
- Modify: `backend/app/schemas/api_ingestion.py`
- Test: `backend/tests/integration/api/test_analysis_jobs.py`
- Modify: `backend/tests/integration/api/test_ingestion_api.py`

**Interfaces:**
- `POST /api/reconciliation-tasks/{task_id}/analysis-jobs`
- `GET /api/analysis-jobs/{job_id}`
- `POST /api/analysis-jobs/{job_id}/retry`
- `POST /api/analysis-jobs/{job_id}/cancel`
- `GET /api/analysis-jobs/{job_id}/events`

- [ ] Write tenant/idempotency/status/retry/cancel API tests and confirm RED.
- [ ] Implement route wiring and task projection, preserving authenticated backend operator identity; confirm GREEN.
- [ ] Add SSE tests for snapshot events, cursor ordering, keepalive format, and cross-tenant 404; confirm RED.
- [ ] Implement a polling-friendly SSE generator that emits only committed state and closes on terminal jobs; confirm GREEN.

### Task 5: Summaries and Batch Adoption Backend

**Files:**
- Create: `backend/app/governance/batch_service.py`
- Create: `backend/app/schemas/batch_governance.py`
- Create: `backend/app/models/proposal_batches.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/repositories/differences.py`
- Modify: `backend/app/repositories/proposals.py`
- Modify: `backend/app/api/routes/proposals.py`
- Modify: `backend/alembic/versions/0010_durable_analysis_jobs.py`
- Test: `backend/tests/integration/governance/test_batch_service.py`
- Test: `backend/tests/integration/api/test_batch_proposals.py`

**Interfaces:**
- `GET /api/reconciliation-tasks/{task_id}/analysis-summary`
- `POST /api/reconciliation-tasks/{task_id}/proposal-batches/preview`
- `POST /api/reconciliation-tasks/{task_id}/proposal-batches/confirm`
- Produces signed preview token and idempotent `BatchProposalResult`.

- [ ] Write aggregation tests with more than one list page and terminal gating; confirm RED, implement grouped query, confirm GREEN.
- [ ] Write mixed preview tests covering executable, high risk, information, manual, failed, stale, and existing proposal; confirm RED.
- [ ] Implement preview token signing and deterministic exclusion classification; confirm GREEN.
- [ ] Write confirmation tests for server-owned content, stale partial success, idempotency, tenant isolation, and unchanged snapshots; confirm RED.
- [ ] Implement immutable batch record plus per-item proposal creation using existing proposal validation; confirm GREEN.

### Task 6: Frontend Job Observation and Chinese Presentation

**Files:**
- Modify: `frontend/src/api/reconciliation.ts`
- Modify: `frontend/src/api/queryKeys.ts`
- Create: `frontend/src/features/workflow/useAnalysisJob.ts`
- Modify: `frontend/src/features/workflow/useReconciliationWorkflow.ts`
- Create: `frontend/src/features/analysis/localization.ts`
- Modify: `frontend/src/features/task-detail/TaskDetailPage.tsx`
- Modify: `frontend/src/features/analysis/AnalysisModal.tsx`
- Test: `frontend/src/api/reconciliation.test.ts`
- Test: `frontend/src/features/workflow/useAnalysisJob.test.tsx`
- Modify: `frontend/src/features/workflow/useReconciliationWorkflow.test.tsx`
- Modify: `frontend/src/features/task-detail/TaskDetailPage.test.tsx`
- Modify: `frontend/src/features/analysis/AnalysisModal.test.tsx`

**Interfaces:**
- Produces TypeScript equivalents of job, v3 resolution, summary, preview, and confirmation contracts.
- `useAnalysisJob` uses SSE when available and a two-second query refetch fallback.

- [ ] Add failing API contract tests for all new endpoints; implement clients/types and confirm GREEN.
- [ ] Add hook tests for SSE update, reconnect, polling fallback, refresh resume, terminal stop, retry, and cancel; implement `useAnalysisJob` and confirm GREEN.
- [ ] Add page tests proving the type summary is absent while running and zero-count types are absent after terminal; update task detail and confirm GREEN.
- [ ] Add modal/localization tests for three v3 modes and Chinese labels for operation/field/risk codes; update modal and confirm GREEN.

### Task 7: AI One-Click Preview and Confirmation UI

**Files:**
- Create: `frontend/src/features/analysis/BatchAnalysisModal.tsx`
- Create: `frontend/src/features/analysis/BatchAnalysisModal.test.tsx`
- Modify: `frontend/src/features/task-detail/TaskDetailPage.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/tests/e2e/reconciliation-flow.spec.ts`

**Interfaces:**
- Modal states: `preview-loading`, `preview`, `confirming`, `result`, `conflict-error`.
- Confirmation sends only preview token and idempotency key, never action content.

- [ ] Write component tests for button visibility, included/excluded sections, explicit pending-execution notice, partial success, conflict refresh, and manual navigation; confirm RED.
- [ ] Implement the modal and task-level action using stable responsive dimensions and Lucide icons; confirm GREEN.
- [ ] Add Playwright assertions for continuous progress -> terminal summary -> batch preview -> pending-execution result; confirm the new scenario fails before fixture/API updates and passes after them.

### Task 8: Full Verification and Documentation

**Files:**
- Modify: `backend/README.md` only if it exists and already documents startup
- Modify: `AGENTS.md` only if the worker command materially changes contributor workflow
- Modify: `openspec/changes/optimize-ai-analysis-workflow/tasks.md`

**Interfaces:** none.

- [ ] Run backend focused tests after each task, then full `.venv/bin/pytest -q`, `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/mypy app`, and `.venv/bin/python -m pip check`.
- [ ] Run frontend `npm test -- --run`, `npm run lint`, `npm run typecheck`, `npm run build`, and Playwright tests.
- [ ] Run Alembic upgrade/downgrade/re-upgrade migration tests and `openspec validate optimize-ai-analysis-workflow`.
- [ ] Mark each OpenSpec checkbox only after its direct implementation and verification evidence exists; do not mark optional visual checks complete without screenshots.

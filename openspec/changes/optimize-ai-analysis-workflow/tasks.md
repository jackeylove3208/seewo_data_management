## 1. Baseline and analysis-v3 contracts

- [x] 1.1 Run focused backend/frontend baselines and record any failures caused by existing uncommitted reconciliation recovery changes
- [ ] 1.2 Add `analysis-v3` Pydantic discriminated-union schemas for auto-executable, needs-information, and manual-only resolution paths with exactly one recommendation
- [ ] 1.3 Add Chinese readability, internal-code, risk, evidence, target, before/after, and allowed-field policy validation with failing unit tests
- [ ] 1.4 Update the analysis Skill, prompt builder, deterministic analysis, corrective retry feedback, and safe fallback to produce concise Simplified Chinese v3 output
- [ ] 1.5 Extend immutable analysis persistence and response conversion for v3 while preserving read-only v1/v2 compatibility

## 2. Durable analysis job persistence

- [ ] 2.1 Add SQLAlchemy models for analysis jobs and analysis work items with statuses, counters, idempotency, attempts, availability, leases, heartbeat, fallback, result link, and audit timestamps
- [ ] 2.2 Create Alembic migration `0010` with PostgreSQL claim indexes, unique constraints, upgrade/downgrade coverage, and imports that preserve the current legacy migration recovery behavior
- [ ] 2.3 Add job/work-item schemas for creation, status, events, retry, cancel, progress counters, and stable localized errors
- [ ] 2.4 Implement repository tests and methods for idempotent creation, current job lookup, `SKIP LOCKED` claim, lease renewal, completion, retry wait, lease recovery, cancel, and counter reconciliation

## 3. Worker and workflow orchestration

- [ ] 3.1 Refactor single-difference analysis so model execution runs without an open database transaction and result persistence occurs in an explicit short transaction
- [ ] 3.2 Implement the analysis job service that creates one work item per current difference version and separates manual-required governance outcomes from technical failures
- [ ] 3.3 Implement a configurable worker loop with bounded concurrency, one-item claiming, heartbeat, transient retry/backoff, stale-difference supersession, and graceful shutdown
- [ ] 3.4 Add worker tests for two-worker contention, lease expiry recovery, retry exhaustion fallback, cancellation, idempotent immutable result reuse, and previously committed result preservation
- [ ] 3.5 Update workflow advancement so the AI stage creates/reuses a job and returns immediately, while task workflow progress reads persisted job state instead of synchronous batches of 10
- [ ] 3.6 Add the documented development worker command without exposing enterprise gateway credentials or changing existing production defaults

## 4. Analysis job APIs and continuous progress

- [ ] 4.1 Add tenant-scoped create/get/retry/cancel analysis job endpoints with idempotency and integration tests
- [ ] 4.2 Add an SSE job event endpoint with monotonic cursors, keepalive events, disconnect handling, and tenant isolation tests
- [ ] 4.3 Add polling-compatible job status and task detail projections with real completed, proposal-ready, needs-information, manual-only, failed, and recent-update values
- [ ] 4.4 Preserve compatibility for existing analysis read endpoints while preventing new v3 jobs from using the legacy synchronous full-task route

## 5. Aggregate summaries and batch proposal adoption

- [ ] 5.1 Add backend aggregation across all current task differences grouped by entity type, independent of list pagination, with terminal-state gating tests
- [ ] 5.2 Add batch preview schemas, preview-token signing, exclusion reason codes, Chinese labels, and task/entity-type scope validation
- [ ] 5.3 Implement batch preview selecting only recommended low/medium-risk v3 auto-executable paths and excluding high-risk, information, manual, failed, stale, and already-proposed items
- [ ] 5.4 Add immutable batch confirmation/idempotency records and migration support needed to return the same partial result on retries
- [ ] 5.5 Implement batch confirmation by server-side copying persisted analysis actions into `pending_execution` proposals with per-item revalidation and no connector writes
- [ ] 5.6 Add integration tests for mixed preview outcomes, client content tampering, stale versions, partial success, duplicate confirmation, tenant isolation, and unchanged target snapshots

## 6. Frontend job progress and localized analysis

- [ ] 6.1 Add TypeScript types, API clients, query keys, and hooks for analysis jobs, SSE/polling progress, aggregate summaries, and batch preview/confirmation
- [ ] 6.2 Replace analysis-stage `workflow/advance` loops with persisted job observation, SSE reconnection, two-second polling fallback, refresh resume, retry, and cancel behavior
- [ ] 6.3 Update task detail to show continuous committed progress and recent-update state, hide the problem-type summary before terminal analysis, and omit zero-issue entity types afterward
- [ ] 6.4 Localize operation, field, entity, risk, status, exclusion, and error codes and render all analysis-v3 content in Chinese without exposing technical codes in normal views
- [ ] 6.5 Update the analysis modal for the three v3 resolution modes while preserving individual AI adoption and schema-driven manual editing

## 7. AI one-click processing experience

- [ ] 7.1 Add a task-level `AI 一键处理` action only when the aggregate summary has proposal-ready items, plus an optional entity-type-scoped secondary action
- [ ] 7.2 Build a batch preview dialog showing included recommendations, before/after changes, exclusions, risk, and the explicit statement that confirmation only creates待执行方案
- [ ] 7.3 Implement confirmation loading, idempotent retry, partial success, conflict refresh, navigation to pending governance execution, and remaining manual-item links
- [ ] 7.4 Add Testing Library coverage for running/terminal visibility, zero-type filtering, Chinese labels, SSE fallback, refresh resume, mixed preview, partial confirmation, and manual-only paths

## 8. End-to-end verification and handoff

- [ ] 8.1 Add PostgreSQL integration coverage for migration, concurrent claims, lease recovery, per-item visibility, and job counter reconciliation
- [ ] 8.2 Add fake enterprise gateway tests proving Chinese v3 repair/fallback, every difference has a resolution path, and protected values remain tokenized
- [ ] 8.3 Add Playwright coverage from completed difference detection through continuous AI progress, terminal type summary, batch preview, and pending-execution proposal creation
- [ ] 8.4 Verify desktop and mobile layouts for progress, type summary, analysis modal, and batch preview with reduced-motion behavior and no overlapping text
- [ ] 8.5 Run backend pytest, Ruff check/format check, mypy, pip check, frontend Vitest, ESLint, TypeScript build, production build, Playwright, migration upgrade/downgrade, and `openspec validate optimize-ai-analysis-workflow`
- [ ] 8.6 Update contributor/development documentation only for new worker and API commands, leaving real model credentials in ignored environment configuration

Baseline on 2026-07-20: backend `pytest -q` passed 275 tests with the real-gateway smoke test skipped; frontend Vitest passed 43 tests. No pre-existing failures were recorded.

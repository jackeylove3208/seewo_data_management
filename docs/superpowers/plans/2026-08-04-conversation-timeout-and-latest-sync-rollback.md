# Conversation Timeout and Latest-Sync Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Agent conversations from remaining indefinitely in “正在理解同步需求”, and make rollback available only for the most recently started Seewo sync task for a target.

**Architecture:** Put the conversation deadline at both API and workbench boundaries, with durable assistant error messages and cleared in-flight claims on timeout. Record the start of every sync task in the existing rollback-cycle state, then derive eligibility from the newest created sync task rather than from the newest successful report. Keep the existing successful-report marker for baseline validation and preserve rollback checkpoints as audit evidence.

**Tech Stack:** FastAPI, SQLAlchemy async services, pytest, React, TypeScript, Vitest, existing rollback-cycle and production graph executors.

## Global constraints

- Work only in `/Users/lbs/PycharmProjects/PythonProject/.worktrees/fix-conversation-and-rollback-policy`.
- Preserve the unrelated sample-data modification in the main worktree.
- Use synthetic fixtures only; do not add real organization records, credentials, or unredacted logs.
- Keep API error codes and rollback reasons stable and explicit so the frontend does not infer behavior from display text.
- Do not add a migration unless the existing task/cycle data cannot express the policy; the preferred implementation derives the latest started task from existing task timestamps and IDs.
- Run focused tests immediately after each behavior change, then run lint/type checks and the relevant full suites before claiming completion.

## 1. Add the implementation plan

- [x] Save this plan under `docs/superpowers/plans/2026-08-04-conversation-timeout-and-latest-sync-rollback.md`.
- [x] Review the plan against the approved design document.
- [ ] Commit the plan with `docs: plan conversation timeout and rollback policy`.

## 2. Add backend conversation timeout regression coverage

**Files:** `backend/tests/integration/api/test_agent_api.py`, plus the smallest test helper/fixture changes needed.

- [ ] Add a test with a deliberately blocking/slow model provider and a very small test timeout setting.
- [ ] Assert the API returns HTTP 504 with `conversation_model_timeout` and the user-facing retry message.
- [ ] Assert the assistant error message is durable and the conversation’s `_message_in_flight` claim is cleared.
- [ ] Assert a later message can attempt the conversation again rather than being rejected by the old lease.
- [ ] Run the focused API test and observe the new test fail before implementation.

## 3. Implement bounded Agent conversation handling

**Files:** `backend/app/core/config.py`, `backend/app/api/routes/agent.py`, `frontend/src/api/agent.ts` if needed by the frontend contract.

- [ ] Add configurable backend model timeout and claim-lease settings with validation that the lease exceeds the model timeout.
- [ ] Wrap the supervisor reply in an `asyncio.timeout` deadline and catch timeout before the generic exception path.
- [ ] On timeout, clear `_message_in_flight`, persist a concise assistant error message, commit, and return the stable 504 error contract.
- [ ] Make claim-active checks use the configured lease rather than a hard-coded duration.
- [ ] Add the frontend request boundary so a hanging request cannot leave the composer pending forever; clear the timer in every completion path and translate timeout into the same stable error contract.
- [ ] Keep normal success, validation, and non-timeout error behavior unchanged.
- [ ] Run the backend conversation tests and the existing frontend conversation tests.

## 4. Add rollback-policy regression coverage

**Files:** `backend/tests/integration/agent_reporting/test_agent_reporting_and_rollback.py`, related rollback-cycle tests if needed.

- [ ] Add a case where an older sync has a fully successful report, then a newer sync task starts before producing a report; assert the older task is immediately stale.
- [ ] Add a case where the newest started sync fails or is partial; assert no older sync can be rolled back and the newest unsuccessful task is not eligible.
- [ ] Add a case where the newest started sync completes fully successfully; assert only that task is eligible.
- [ ] Assert the stale reason is `stale_sync_record` and the API-facing failure is distinguishable from “already rolled back”.
- [ ] Cover direct service calls and the common task-creation path so the start marker cannot be skipped by a normal sync task.
- [ ] Run the focused rollback tests and observe the new cases fail before implementation.

## 5. Implement latest-started-sync rollback eligibility

**Files:** `backend/app/agent_reporting/rollback_cycles.py`, `backend/app/agent_runtime/task_service.py`, `backend/app/schemas/agent_api.py`, `backend/app/api/routes/agent.py`.

- [ ] Add a `record_sync_started(task)` operation invoked after the sync task is flushed in the shared task creation service.
- [ ] Advance the existing rollback-cycle generation and clear completed-rollback state when a new sync starts; do not mark the new task successful until its report is verified.
- [ ] Resolve the newest sync task for the same target by creation order and use it as the sole rollback source. This must include tasks that have no report, failed, or partial status.
- [ ] Reject every older source with reason `stale_sync_record`, including historical tasks where no rollback-cycle row exists yet.
- [ ] Preserve the existing successful-report marker for target baseline checks, and preserve existing generation/conflict protections for rollback previews and confirmations.
- [ ] Add the new rollback error reason literal and return HTTP 409 code `rollback_sync_record_too_old` with “记录过旧，无法回滚”.
- [ ] Keep rollback safety checks ordered so stale-source rejection cannot be bypassed by a cached preview or by a successful older report.
- [ ] Run the focused backend rollback and task API tests.

## 6. Fix rollback execution routing and terminal failure handling

**Files:** `backend/app/agent_graph/production_executor.py`, worker/action state code and tests identified by `rg`, `backend/tests/integration/agent_graph/test_production_runtime.py`, `backend/tests/integration/agent_graph/test_worker.py`.

- [ ] Add a regression test proving a legacy database rollback uses the SQL/database handler and never invokes the CSV handler.
- [ ] Route legacy database and CSV rollback finalization to their matching handlers; remove the unconditional CSV execution path.
- [ ] Convert connector/runtime rollback failures into a durable terminal failed state with a stable safe error code/message, while retaining recovery/audit details and avoiding a generic `running` reclaim loop.
- [ ] Preserve special handling for uncertain external writes and graph/model failures where their existing audit semantics are required; update only the real rollback execution path.
- [ ] Assert locks are released and the task/event state is terminal after the runtime failure.
- [ ] Run the focused production-runtime and worker tests, including existing recovery tests.

## 7. Surface stale rollback state in the workbench

**Files:** `frontend/src/api/agent.ts`, `frontend/src/features/task-detail/AgentTaskDetailPage.tsx`, corresponding frontend tests.

- [ ] Add `stale_sync_record` to the typed rollback-blocked reasons.
- [ ] Render “记录过旧，无法回滚” and keep the rollback action disabled for stale records.
- [ ] Add a UI regression test for the stale response and retain the existing already-rolled-back behavior.
- [ ] Run the focused task-detail and conversation tests.

## 8. Verify and hand off

- [ ] Run `git diff --check` and inspect the complete diff for unrelated changes.
- [ ] Run backend focused tests, then `backend/.venv/bin/pytest`, `backend/.venv/bin/ruff check .`, and `backend/.venv/bin/mypy app` as available.
- [ ] Install worktree frontend dependencies with `npm ci --include=dev --cache /private/tmp/codex-agent-report-npm-cache` if needed, then run `npm test -- --run`, `npm run lint`, `npm run typecheck`, and `npm run build`.
- [ ] Run the clean PostgreSQL migration smoke test if Docker/PostgreSQL is available.
- [ ] Re-check the worktree status and report exact tests and any environment-limited checks.


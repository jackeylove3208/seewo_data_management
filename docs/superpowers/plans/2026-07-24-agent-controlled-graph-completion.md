# Agent Controlled Graph Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining `agent-graph-v1` correctness, recovery, interaction, observability, and real-browser gaps while preserving the user-approved risk policy where only deletion and student-phone changes are high risk.

**Architecture:** Keep the existing server-owned controlled graph and append-only audit model. Strengthen deterministic evidence and replay boundaries first, then complete human gates and progress APIs, and finally run a real FastAPI/worker/Vite browser journey. No task may reintroduce legacy delegation or allow the model to construct connector writes.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL/SQLite tests, Pydantic v2, React, TypeScript, TanStack Query, Vitest, Playwright.

## Global Constraints

- Work only in `.worktrees/agent-supervisor-controlled-graph`; do not merge to `master`.
- Keep `legacy-v1` and `new-agent-v1` behavior unchanged.
- Only deletion and student-phone changes are high risk in this demo.
- Third-party authority data remains read-only.
- Every behavior change follows RED → GREEN → REFACTOR.
- Do not stage `.serena`, `backend/.venv`, `frontend/node_modules`, or local CSV fixtures.

---

### Task 1: Complete post-identity field comparison

**Files:**
- Modify: `backend/app/reconciliation/agent_identity.py`
- Modify: `backend/tests/unit/reconciliation/test_agent_identity.py`
- Modify: `backend/tests/e2e/test_agent_graph_lifecycle.py`

**Interfaces:**
- Consumes: accepted authority/target identity claims.
- Produces: `ordinary_field_differences(authority, target) -> tuple[str, ...]` containing every applicable governed field that differs.

- [ ] Add a failing unit test proving number, phone, and email remain governed differences after another identity key establishes correspondence.
- [ ] Run the focused test and confirm it fails because those fields are absent.
- [ ] Include `number`, `phone`, and `email` in deterministic comparison while retaining student-only `class_name`.
- [ ] Add an end-to-end assertion that a wrong phone/email produces an actionable AI finding and solution.
- [ ] Run focused identity and graph lifecycle tests.

### Task 2: Bind complete paired evidence manifests

**Files:**
- Modify: `backend/app/agent_graph/evidence.py`
- Modify: `backend/app/agent_graph/analysis_tools.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Modify: `backend/tests/unit/agent_graph/test_evidence.py`
- Modify: `backend/tests/integration/agent_graph/test_tools.py`
- Modify: `backend/tests/integration/agent_graph/test_real_subagents.py`

**Interfaces:**
- Consumes: graph context, snapshots, current target version, identity work, claims, candidates, and issued phone tokens.
- Produces: immutable `EvidenceManifestV1` and typed `PairedRecordEvidenceV1` whose members exactly bound every model-visible reference.

- [ ] Add failing tests requiring opaque tenant references, snapshot pair, target version, and issued phone-token membership.
- [ ] Add failing tests for complete paired evidence: key hits, conflicts, claims, stable order, field differences, candidates, and allowed operations.
- [ ] Build manifest facts from durable snapshots/target versions rather than empty defaults.
- [ ] Replace reversible tenant strings with an HMAC-derived opaque reference.
- [ ] Return and validate complete paired evidence without exposing raw student phone values.
- [ ] Run evidence, tool-security, and real-sub-agent tests.

### Task 3: Make graph retry and invocation replay durable

**Files:**
- Modify: `backend/app/agent_graph/runtime.py`
- Modify: `backend/app/agent_graph/worker.py`
- Modify: `backend/app/agent_graph/repository.py`
- Modify: `backend/app/ai/graph_subagents.py`
- Modify: `backend/tests/integration/agent_graph/test_worker.py`
- Modify: `backend/tests/integration/agent_graph/test_real_subagents.py`
- Modify: `backend/tests/unit/agent_graph/test_guards.py`

**Interfaces:**
- Consumes: frozen candidate set, decision, manifest, invocation attempt, tool-call trace, and work-unit retry counter.
- Produces: replay decisions that reuse completed outcomes, safely resume incomplete attempts, and expose real bounded repair actions.

- [ ] Add a failing test for crash after a completed Skill invocation but before graph transition.
- [ ] Add a failing test for crash after an authorized tool result.
- [ ] Add failing tests for reread, renormalize, and repair-analysis actions with a three-entry budget and fourth-failure blocking.
- [ ] Make manifest and invocation idempotency keys stable for one cursor/action/input hash.
- [ ] Reuse completed invocation outputs and resume/replace only safely incomplete attempts.
- [ ] Persist and increment node/work-unit retry and replan counters with safe failure codes.
- [ ] Implement real `repair_analysis_batch` execution rather than guarded no-op.
- [ ] Run worker, sub-agent, lease, and crash-recovery tests.

### Task 4: Complete rollback and human-gate semantics

**Files:**
- Modify: `backend/app/agent_graph/rollback_executors.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Modify: `backend/app/agent_graph/governance_executors.py`
- Modify: `backend/app/api/routes/agent.py`
- Modify: `backend/app/schemas/agent_graph_api.py`
- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.tsx`
- Modify: related backend and frontend gate tests.

**Interfaces:**
- Consumes: frozen rollback operation IDs, restore conflicts, operator text, and termination request.
- Produces: one verified rollback operation per tool call, interpreted rollback-conflict decisions with second confirmation, and explicit termination confirmation.

- [ ] Add a failing test proving requesting one rollback operation cannot execute another.
- [ ] Add failing API/UI tests for rollback conflict text → model interpretation → second confirmation.
- [ ] Add failing API/UI tests for termination preview/confirmation before `termination_requested` is persisted.
- [ ] Implement per-operation rollback execution and dependency-safe continuation.
- [ ] Reuse the bounded conflict Skill pattern for restore conflicts.
- [ ] Add a typed termination-confirmation gate and modal without allowing the Supervisor to terminate directly.
- [ ] Run rollback, human-gate, API, and task-detail tests.

### Task 5: Populate Supervisor context and strengthen guards

**Files:**
- Modify: `backend/app/agent_graph/supervisor.py`
- Modify: `backend/app/agent_graph/runtime.py`
- Modify: `backend/app/agent_graph/actions.py`
- Modify: `backend/app/agent_graph/guards.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Modify: associated contract, runtime, and production integration tests.

**Interfaces:**
- Consumes: durable blockers, completed/pending work, manifests, gates, connector capabilities, approvals, operation readiness, and target version.
- Produces: complete `SupervisorContextV1`, semantically accurate singleton reasons, privacy-filtered audit narration, and fail-closed preflight results.

- [ ] Add failing tests for non-empty production context summaries and correct singleton reason codes.
- [ ] Add failing tests that raw phone/internal resource identifiers cannot survive in `operator_message_zh`.
- [ ] Add failing preflight tests for stale finding/solution, approval hash, expected-before, capability, dependency, and target version.
- [ ] Populate the Supervisor context from server facts without exposing payload rows.
- [ ] Sanitize non-executing Supervisor narration before persistence and display.
- [ ] Centralize preflight guard results and reject advancement on any failed fact.
- [ ] Run contract, guard, production-runtime, and security tests.

### Task 6: Expose useful live progress and real browser recovery

**Files:**
- Modify: `backend/app/schemas/agent_graph_api.py`
- Modify: `backend/app/api/routes/agent.py`
- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.tsx`
- Modify: `frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx`
- Modify: `frontend/tests/e2e/agent-graph.spec.ts`
- Create or modify: synthetic browser-test launcher/fixtures under `frontend/tests/e2e/`.

**Interfaces:**
- Consumes: graph state, active action, sub-agent invocation, batch counts, operation counts, and human gates.
- Produces: Chinese business progress with sub-agent label and bounded `completed/total` counts that survives navigation and refresh.

- [ ] Add failing React tests for sub-agent name, batch progress, rollback dialogue, and termination modal.
- [ ] Add a failing Playwright journey that starts through the manual CSV entry and reloads during processing.
- [ ] Add a failing Playwright journey that starts through the conversation entry and reaches backend history.
- [ ] Extend the graph progress API without exposing node IDs, hashes, prompts, or raw phones in rendered UI.
- [ ] Replace route-only graph E2E coverage with a synthetic FastAPI/worker/Vite journey for start, approval, report, and independent rollback.
- [ ] Run Vitest and Playwright on isolated ports.

### Task 7: Add graph observability and complete delivery gates

**Files:**
- Modify: `backend/app/agent_runtime/observability.py`
- Modify: `backend/app/agent_graph/worker.py`
- Modify: `backend/app/agent_graph/tools.py`
- Modify: `backend/app/agent_graph/repository.py`
- Modify: `backend/app/agent_runtime/README.md`
- Modify: `README.md`
- Modify: observability and launcher tests.

**Interfaces:**
- Consumes: transition timestamps, queue/lease facts, retry/replan counts, tool calls, gates, writes, and rollback outcomes.
- Produces: privacy-safe structured metrics and a reproducible one-command demo startup/runbook.

- [ ] Add failing tests for node duration, replan count, tool-call count, gate wait, and rollback outcome metrics.
- [ ] Emit bounded metrics without row contents, credentials, paths, or raw phones.
- [ ] Document `python3 dev.py`, feature flags, model requirements, lock lifecycle, failure recovery, and browser URL.
- [ ] Run backend full pytest with `--import-mode=importlib`, Ruff, mypy, and clean PostgreSQL migration.
- [ ] Run frontend Vitest, lint, typecheck, production build, and real Playwright journeys.
- [ ] Run `openspec validate new-agent-architecture`, `python3 dev.py --dry-run`, and `git diff --check`.
- [ ] Stage only intended source/tests/docs and commit the completed work on the feature branch without merging.

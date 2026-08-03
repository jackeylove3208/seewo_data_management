# Retired workflow cleanup design

## Goal

Remove the creation and execution paths for `legacy-v1` and `new-agent-v1` while preserving the
observable behavior, safety boundaries, and stored data of `agent-graph-v1`.

## Supported product boundary

`agent-graph-v1` becomes the only workflow version accepted for new tasks and the only version
claimed by a reconciliation worker. The supported interactive frontend consists of conversation
task creation, Graph task history and detail, Graph approvals, Graph reports, and Graph rollback.

Historical `legacy-v1` and `new-agent-v1` rows remain stored and may be listed or displayed as
archived read-only summaries. They cannot be advanced, retried, terminated, approved, executed, or
used to create a rollback task. This cleanup does not delete historical rows or rewrite their
workflow versions.

## Version taxonomy

The `v1` suffix in `agent-graph-v1` does not mean that the task uses only first-generation
implementations. `workflow_version` is the persisted top-level routing family. A current
`agent-graph-v1` run can select newer nested contracts independently:

- `graph_version`: `agent-sync-graph-v1`, `agent-sync-graph-v2`, or
  `agent-rollback-graph-v1`;
- `ingestion_contract_version`: `source-ingestion-v2` or `source-ingestion-v3` for current
  deterministic ingestion, with older persisted contract values retained for resumability;
- `execution_contract_version`: `deterministic-execution-v2` for current deterministic
  execution, with older persisted contract values retained for resumability;
- versioned mapping, adapter, projection, Skill, evidence, checkpoint, and report contracts used
  beneath those graph runs.

All nested versions reachable from an `agent-graph-v1` task are current Graph code and are outside
the deletion scope. A `v1`, `v2`, or `v3` suffix is never evidence that a definition is obsolete.
Only the top-level retired workflow families `legacy-v1` and `new-agent-v1`, plus code proven to be
exclusive to their creation or execution, are cleanup candidates.

## Reachability rule

A production definition or file may be deleted only when it is unreachable from every retained
root after retired entry points are removed. The retained roots are:

- `app.main` with health, conversation/Agent, API-connector, and Graph-required routes;
- `python -m app.agent_runtime` with the Graph worker and connector maintenance;
- Alembic migration loading and SQLAlchemy model metadata;
- the React `main.tsx` route tree for conversation creation, task history, Graph detail, Graph
  report, and Graph-supported execution interactions;
- historical task list and archived-summary reads;
- test and development launchers for the retained roots.

Text search alone is insufficient proof of dead code. Deletion requires checking imports, dynamic
entry points, package data, configuration, tests, and the relevant Git history. Code reused by the
Graph executor remains even when its filename or original purpose predates the Graph runtime.
The audit must also trace nested graph, ingestion, execution, mapping, adapter, projection, Skill,
evidence, and checkpoint versions before classifying a branch as retired.

## Backend changes

New task submission fails closed unless the configured workflow is `agent-graph-v1`; configuration
no longer silently falls back to `new-agent-v1` or `legacy-v1`. The traditional reconciliation
upload/task endpoints and the fixed Agent creation path stop creating retired workflow rows.

The runtime launcher stops constructing the fixed `new-agent-v1` worker. The Graph worker retains
its exact version-filtered claim behavior. Shared runtime contracts, repositories, school-lock
handling, governance handlers, rollback handlers, reporting facts, connector implementations, and
model-provider code remain whenever Graph imports them.

Standalone legacy workflow, matching, difference-analysis, proposal, and legacy AI-job code is
removed only after its API routes and worker entry points are removed and a reference audit proves
that no retained root imports it. Mixed modules are simplified by deleting retired branches without
changing their `agent-graph-v1` branches.

HTTP mutations aimed at historical retired tasks return a stable conflict response explaining that
the workflow is archived. Unknown tasks continue to return the existing not-found response. No
retired task is allowed to acquire or heartbeat a school lock after the cleanup.

## Frontend changes

The `/tasks/new` manual synchronization route and its dedicated page are removed. Task creation
continues through `/conversations/new`. Legacy workflow controls, classic matching/difference
pages, legacy analysis modals and hooks, and tests dedicated solely to those interactions are
removed when they are not used by Graph pages.

Task routing first identifies the persisted workflow version. `agent-graph-v1` opens the existing
Graph detail experience unchanged. Historical `legacy-v1` and `new-agent-v1` tasks render a small
archived, read-only summary with no mutation controls. Graph task deletion retains its current
safety checks.

## Data and migration safety

All existing Alembic revisions remain. SQLAlchemy columns, tables, enum-compatible string values,
foreign keys, immutable audit records, and package data needed to load an existing database remain.
There is no data purge in this cleanup.

Before removing old workers, a read-only audit identifies nonterminal retired runs and active school
locks. The code change does not silently mark them complete or delete them. If such rows exist in a
real deployment, operators must terminate/archive them and release their lock through an explicit
separate operational action before deploying the cleanup.

## Incremental deletion strategy

1. Characterize the current Graph creation, execution, report, rollback, and frontend flows.
2. Make task creation Graph-only and add archived-workflow mutation rejection.
3. Remove the manual legacy frontend entry and render retired tasks read-only.
4. Stop launching and claiming fixed `new-agent-v1` work.
5. Remove standalone retired backend routes and modules in small dependency-ordered batches.
6. Remove tests that exercise only deleted behavior; retain or add boundary tests proving Graph
   behavior and historical read-only behavior.
7. Recompute production/test file and line counts and run the complete delivery gates.

Because the cleanup removes more than 500 lines, deletions use whole-file removal or mechanical,
reviewable edits rather than manually rewriting large implementations.

## Verification

Each batch must pass focused characterization tests before and after deletion. Existing Graph tests
must not be rewritten merely to accommodate a behavior change. The final gate includes backend
pytest, Ruff, mypy, the clean PostgreSQL migration smoke test, frontend Vitest, ESLint, TypeScript
typecheck, Vite build, Playwright tests where the browser environment is available, OpenSpec strict
validation, final reference scans for retired creation/worker entry points, and `git diff --check`.

The final report includes deleted files and lines, remaining production/test counts, any retained
old-looking modules with their Graph consumer, and any verification that could not run because of an
external environment dependency.

## Non-goals

- No change to Graph decisions, prompts, evidence contracts, risk policy, approvals, connectors,
  mutations, verification, reporting facts, or rollback semantics.
- No removal of `agent-sync-graph-v1`, `agent-sync-graph-v2`, `agent-rollback-graph-v1`,
  `source-ingestion-v2`, `source-ingestion-v3`, `deterministic-execution-v2`, or any nested
  contract used or resumed by `agent-graph-v1`.
- No deletion or rewriting of historical task data.
- No Alembic history squashing.
- No cosmetic refactor of retained Graph code.
- No removal based solely on age, naming, or low test coverage.

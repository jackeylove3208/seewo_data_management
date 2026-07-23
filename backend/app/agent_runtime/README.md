# Agent runtime foundation

This package owns the durable `new-agent-v1` workflow. Start it separately from the API:

```bash
.venv/bin/python -m app.agent_runtime
```

The process logs a startup line immediately, then polls quietly until Agent work is available.

For complete CSV governance in the demo, use these flags in `backend/.env`:

```dotenv
RECONCILIATION_NEW_AGENT_ENABLED=true
RECONCILIATION_NEW_AGENT_ANALYSIS_ONLY=false
RECONCILIATION_NEW_AGENT_CSV_EXECUTION_ENABLED=true
RECONCILIATION_TOKENIZATION_SECRET=replace-with-at-least-16-characters
```

Start PostgreSQL, migrate, then run the API and Agent worker in separate terminals:

```bash
docker compose -f ../infra/docker-compose.yml up -d
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
.venv/bin/python -m app.agent_runtime
```

## Compatibility boundary

- Existing historical CSV API tasks persisted as `legacy-v1` continue through
  `matching -> differences -> analysis`.
- Agent tasks are persisted as `new-agent-v1`; legacy matching, difference detection, durable
  analysis jobs, vector/rematching, and matching-quality services reject that version before
  performing work.
- `AgentWorker` claims only rows from `agent_runs`. The existing AI worker continues to claim
  only `analysis_work_items`, so the two runtimes cannot consume each other's jobs.
- The rollout defaults are disabled and analysis-only. Enabling an execution flag without the
  Agent runtime, or enabling CSV execution while analysis-only is active, fails configuration.
- Disabling the rollout does not rewrite an existing task's immutable `workflow_version`.

## Demo identity boundary

For this demo, the trusted school identifier is `OperatorContext.tenant_id`, supplied by the
backend dependency. Clients cannot submit or override it. A future authentication and school
selection system replaces only the `OperatorContext` provider; the Agent run, school lock, task,
event, and audit models remain unchanged. Login, school switching, and role administration are
outside this change.

## Durable invariants

- A database partial unique index permits only one active school lock per tenant.
- Runs, ordered events, checkpoints, safe failures, worker attempts, leases, and lock releases
  are persisted independently of browser state.
- Phase handlers receive an immutable work context rather than a writable repository. Their
  result is committed only after the worker revalidates lease ownership; heartbeat loss cancels
  the handler and prevents a stale phase transition.
- The state machine permits only the next server-owned phase. Terminal runs are immutable.
- Model work receives one initial attempt plus no more than three retries. Exhaustion records a
  sanitized failure, blocks the run, and leaves the school lock active.
- Skills are pinned by name/version/phase and bind resolvable, strict Pydantic input/output
  envelopes. Agent tool calls must pass both the context capability allowlist and the
  server-owned phase allowlist.

## Lock and failure diagnostics

`GET /api/agent/active-lock` returns only the current demo tenant's active owner task/run and
timestamps. `GET /api/agent/tasks/{task_id}` and
`GET /api/agent/tasks/{task_id}/events?cursor=...` expose sanitized phase progress and failure
events; they never include CSV row bodies, credentials, raw student phone, prompts, or stack
traces.

Do not manually delete a live `school_task_locks` row. If a model batch exhausts the initial
attempt plus three retries, the task intentionally retains the lock and permits only
`POST /api/agent/tasks/{task_id}/terminate`. Termination first persists its report and then
releases the lock. A worker crash is recovered through its lease/checkpoint; restart the Agent
worker and let it reclaim the incomplete phase. If the report phase itself repeatedly fails,
inspect sanitized events and database availability, restore the dependency, restart the worker,
and terminate only when the operator explicitly chooses to abandon the task.

## Connector rollout boundary

CSV is the only connector currently bound end-to-end to the durable Agent run. The safe
configured API/database connector façades and contract tests exist, but task submission fails
before task/lock creation with `connector_capability_failure` until connector input evidence and
mutation sessions are durably bound to the worker. This fail-closed boundary prevents a
configuration-only connector selection from becoming a stuck task.

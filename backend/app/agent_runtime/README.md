# Agent runtime foundation

This package is the durable foundation for `new-agent-v1`. It intentionally does not replace
the existing reconciliation pipeline yet.

## Compatibility boundary

- Existing and current CSV API tasks are persisted as `legacy-v1` and continue through
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

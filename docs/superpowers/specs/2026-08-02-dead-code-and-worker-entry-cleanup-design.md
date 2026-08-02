# Dead code and worker entry cleanup design

## Goal

Remove code that has no production consumer and make every documented development launcher start the worker that handles current Agent workflows, without changing reconciliation behavior or public API responses.

## Scope

The cleanup has two independent, reviewable parts:

1. Align `frontend/scripts/dev.mjs`, its tests, and `AGENTS.md` on `python -m app.agent_runtime`. The root launcher and repository READMEs already use this entry point. `app.ai.worker` remains available as an internal legacy analysis-worker dependency because `app.agent_runtime` imports its shared worker loop.
2. Remove definitions proven to have no production references:
   - the obsolete `app.ai.mcp.agent_gateway` module and its dedicated integration test;
   - `ConnectorNotConfigured`;
   - `AnalysisJobCreateRequest` and its dedicated validation test;
   - `CARDINALITY_STATUSES` and `STABLE_KEYS`;
   - unused short aliases for execution ORM records;
   - the test-only `load_frozen_database_mapping` wrapper;
   - the duplicate public `identity_postings` helper, retaining `_record_postings` as the single implementation used by production.

The cleanup deliberately retains `AgentRetryableTargetError` and `RetryableConnectorError`. They express supported retry contracts and may be used by future or out-of-tree connector implementations even though current in-repository production adapters do not instantiate them.

## Behavior and compatibility

No HTTP request or response schema changes. `AnalysisJobCreateRequest` is not bound to an endpoint; analysis-job creation continues to read `Idempotency-Key` from the request header. No database schema or migration changes.

The development launcher behavior changes only for the worker command. With new Agent flags enabled, `npm run dev` will run the fixed and graph workers through `app.agent_runtime`; legacy analysis support remains reachable through the worker set assembled by that entry point.

Tests that exist solely to exercise deleted, unreachable code will be removed. Tests for active replacements and production behavior remain unchanged. Where a test currently calls a redundant wrapper, it will call the production context loader and assert its `mapping` value instead.

## Verification

- Frontend launcher unit tests must assert `app.agent_runtime`.
- Focused backend tests must cover database mapping context loading and identity posting behavior through active production paths.
- Ruff, mypy, ESLint, TypeScript typecheck, frontend build, and both full test suites must pass.
- A final reference scan must show no imports of the deleted module or removed symbols.

## Non-goals

- No redesign of Agent workflow versions.
- No removal of `app.ai.worker` or fixed `new-agent-v1` support.
- No consolidation of generic hashing, path, or serialization helpers outside the confirmed dead-code list.
- No unrelated formatting or naming changes.

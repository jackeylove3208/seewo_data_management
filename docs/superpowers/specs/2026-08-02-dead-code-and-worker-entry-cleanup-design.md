# Dead code and worker entry cleanup design

## Goal

Remove code that has no production consumer without changing worker startup, reconciliation behavior, or public API responses.

## Scope

The cleanup removes definitions proven to have no production references:
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

The development launchers retain their existing responsibilities. `npm run dev` and `AGENTS.md` continue to use `app.ai.worker`, which starts under the repository's default-off Agent configuration and consumes legacy analysis jobs. The controlled Agent demo continues to use `app.agent_runtime` through `dev.py` and the README instructions. Unifying those worker families requires a separate design because `app.agent_runtime` currently requires enabled Agent flags and does not construct `AnalysisWorker`.

Tests that exist solely to exercise deleted, unreachable code will be removed. Tests for active replacements and production behavior remain unchanged. Where a test currently calls a redundant wrapper, it will call the production context loader and assert its `mapping` value instead.

## Verification

- Frontend launcher unit tests must continue to assert `app.ai.worker`.
- Focused backend tests must cover database mapping context loading and identity posting behavior through active production paths.
- Ruff, mypy, ESLint, TypeScript typecheck, frontend build, and both full test suites must pass.
- A final reference scan must show no imports of the deleted module or removed symbols.

## Non-goals

- No redesign of Agent workflow versions.
- No removal of `app.ai.worker` or fixed `new-agent-v1` support.
- No unification of legacy analysis and Agent worker entry points.
- No consolidation of generic hashing, path, or serialization helpers outside the confirmed dead-code list.
- No unrelated formatting or naming changes.

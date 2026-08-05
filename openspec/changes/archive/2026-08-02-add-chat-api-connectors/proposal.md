## Why

The Agent conversation can describe an API connector, but the task runtime still rejects
`api + database` and cannot turn a live organization API into the `AgentInputRecord` evidence
consumed by the current identity, analysis, and governance pipeline. We need one secure,
versioned API-ingestion contract so DingTalk, WeCom, and later providers can reuse the existing
Agent Graph without publishing a graph per provider or exposing credentials to a model.

## What Changes

- Add a tenant-scoped organization API connection control plane with audited provider manifests,
  secret references, connection tests, capability/visibility summaries, and sanitized errors.
- Add DingTalk and WeCom organization adapters behind one deterministic provider interface.
- Add immutable API authority materialization that captures complete paginated evidence before
  source inspection and normalization.
- Add `source-ingestion-v3`, replacing one task-wide pair mode with authoritative/target role
  bindings so an API authority can reconcile against a MySQL target.
- Route new API tasks through `agent-sync-graph-v2` and extend its existing
  `materialize_sources` action to dispatch `api-source` resources without adding graph nodes.
- Normalize frozen API evidence to `AgentContractRecord` and then `AgentInputRecord`; do not
  create legacy raw/canonical/entity-mapping records.
- Extend Agent identity construction with explicit external identity bindings, safe handling for
  authority records without ordinary identity keys, and unavailable-field semantics.
- Keep existing analysis batches, risk policy, approvals, SQL governance execution, verification,
  and reporting contracts unchanged.
- Freeze graph, ingestion, execution, provider, and adapter versions at run creation so historical
  runs resume under their original contracts.

## Capabilities

### New Capabilities

- `organization-api-connectors`: Tenant-scoped provider registration, secure connection
  configuration, deterministic connection testing, immutable API capture, and DingTalk/WeCom
  adapter behavior.
- `agent-external-identity-binding`: Audited cross-run bindings from an API authority locator to a
  target database locator, including stale/conflicting binding handling.

### Modified Capabilities

- `agent-data-ingestion`: Accept API authority/database target role bindings, normalize frozen API
  evidence to Agent inputs, and distinguish unavailable API fields from explicit empty values.
- `multi-agent-reconciliation-runtime`: Select Graph v2 and ingestion v3 for API tasks while
  preserving frozen historical run versions.
- `conversational-task-creation`: Let a conversation select or configure an organization API
  connection without putting credentials in model-visible conversation state.

## Impact

- Backend task creation, supervisor version selection, graph candidate generation, graph action
  execution, source inspection/normalization, identity indexing, configuration, API routes, models,
  repositories, migrations, and audit/security tests.
- New provider registry, API connection service, secret resolver boundary, API materializer, API
  ingestion adapter, DingTalk adapter, and WeCom adapter.
- Conversation contracts and frontend cards for secure connector configuration, connection
  status, provider selection, and safe errors.
- Existing CSV/local, remote CSV/local, and database/database tasks must retain their current
  behavior and recovery contracts.

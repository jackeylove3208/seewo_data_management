## Context

The current Agent task schema accepts an `api` connector selection, but
`AgentTaskService` rejects mixed `api + database` execution. Source ingestion v2 routes an entire
task through one pair mode, Graph v2 materializes only conversation remote CSV, and authoritative
completeness assumes every fixed field is visible. The downstream runtime already has the desired
contract: normalized `AgentInputRecord` rows feed identity postings, claims, work items, AI batches,
risk aggregation, approvals, deterministic SQL execution, verification, and reports.

This change implements the API-ingestion boundary described in
`docs/superpowers/specs/2026-07-29-chat-driven-api-connectors-design.md`. It must preserve existing
CSV/local, remote CSV/local, database/database, historical Graph, and historical ingestion behavior.

## Goals / Non-Goals

**Goals:**

- Configure and test tenant-scoped DingTalk and WeCom organization connections without exposing
  credentials to a model.
- Capture a complete API authority snapshot and normalize it to the existing six-field Agent input
  contract.
- Reconcile an API authoritative role against a MySQL target role through the current Graph v2
  topology.
- Preserve deterministic replay, school exclusivity, target version checks, SQL idempotency, and
  safe reporting.
- Support explicit, audited external identity bindings and unavailable API fields.
- Make later providers an Adapter/Manifest addition rather than a Graph addition.

**Non-Goals:**

- Model-generated endpoints, arbitrary OpenAPI import, arbitrary HTTP tools, or credentials in chat.
- Third-party writes or API targets.
- A supplier-specific graph, graph node, OAuth Skill, HTTP Skill, or normalization Skill.
- Replacing the existing AI batch, governance, SQL execution, verification, or rollback contracts.
- Treating a provider userid, unionid, or department ID as a school business number.

## Decisions

### Reuse Graph v2 and add ingestion v3

New API tasks freeze:

```text
workflow_version=agent-graph-v1
graph_version=agent-sync-graph-v2
ingestion_contract_version=source-ingestion-v3
execution_contract_version=deterministic-execution-v2
```

Graph v2 already places `materialize_sources` after the school lock and before inspection. A new
Graph version would duplicate topology without changing server-owned transitions. Existing runs
retain their stored versions and never derive a newer contract during resume.

Alternative considered: publish `agent-sync-graph-v3`. Rejected because no node, transition, guard,
or human-gate topology changes.

### Route ingestion by role binding

Ingestion v3 resolves one immutable binding for `authoritative` and one for `target`. Each binding
contains role, connector kind, configuration ID, snapshot ID, mapping checkpoint key, and
normalization checkpoint key. API authority and database target inspection, mapping, and
normalization are selected independently.

Alternative considered: extend `_source_pair_mode()` with an `api_database` value. Rejected because
it preserves the coupling that will fail again when another source/target combination is added.

### Use a common provider registry with audited adapters

A provider manifest declares provider ID, adapter version, supported entities, credential schema,
capability requirements, endpoint policy, and field projection version. DingTalk and WeCom each
implement the same deterministic Adapter protocol but keep supplier-specific authentication,
pagination, errors, and organization semantics.

Alternative considered: a universal model-driven HTTP connector. Rejected because it makes network
behavior non-deterministic and exposes an unacceptable credential and SSRF boundary.

### Materialize API authority before inspection

`materialize_sources` keeps the Graph v2 action kind `materialize_remote_authority` and dispatches by
resource prefix:

```text
remote-source:<id> -> existing remote CSV materializer
api-source:<id>    -> API authority materializer
```

The API materializer writes a temporary canonical JSONL stream, validates pagination closure and
external-ID uniqueness, then atomically publishes a managed `SourceFile` and authoritative
`Snapshot`. Inspection and normalization read only that frozen artifact.

Alternative considered: call the API during normalization. Rejected because token expiry and
mid-run source changes would make replay and evidence hashes unreliable.

### Terminate API ingestion at AgentInputRecord

`AgentApiIngestionAdapter` projects frozen provider records to `AgentContractRecord`;
`AgentAnalysisRepository.persist_inputs()` persists `AgentInputRecord` and input marks. API tasks do
not create `RawSnapshotRow`, `CanonicalEntityRecord`, or legacy `EntityMapping`.

Alternative considered: reuse the legacy canonical pipeline. Rejected because current identity and
analysis read Agent input, claim, and work-item tables rather than legacy canonical entities.

### Keep technical external IDs outside ordinary identity postings

API stable locators use:

```text
api:<connection_id>:<entity_kind>:<encoded_external_id>
```

Ordinary postings remain limited to number, phone, and email. A new
`AgentExternalIdentityBindingRecord` can map an authority stable locator to a target database stable
locator. The identity builder validates bindings before ordinary posting lookup and creates the
normal per-run `AgentIdentityClaimRecord`.

Alternative considered: copy userid into `number`. Rejected because a provider technical ID is not
a school employee, student, or department business number.

### Represent unavailable fields as input evidence

An API adapter records `authority_field_unavailable` with affected fields when provider permission
or visibility prevents reading a field. Identity comparison excludes those fields. An explicit
empty value remains a governed value and can only clear target data through the existing policy and
approval path.

Authority records without number, phone, or email remain eligible for external-binding lookup. If
no valid binding exists, identity construction records `authority_identity_absent` and creates an
`authority_invalid` work item instead of asking a model to guess.

### Keep credentials behind a backend-only resolver

Connection records store public configuration and an opaque `secret_ref`. Only the provider runtime
resolves secrets. Graph state, task intent, checkpoints, Skill payloads, MCP arguments, events,
errors, and logs contain only safe connection views and sanitized codes.

Task creation copies the safe public configuration and opaque secret version reference into the
task-bound API source. Secret rotation retains encrypted versions while a task references them, so
materialization never rereads mutable connection inputs or silently changes organizations.

Connection testing and secure configuration occur outside a sync Graph and never write target data.

## Risks / Trade-offs

- **Provider fields are hidden by tenant permissions** → Persist visibility summaries and
  unavailable-field marks; never infer that hidden means empty.
- **Provider pagination changes during capture** → Validate cursor progress, duplicate IDs, page
  limits, and final closure; publish only an atomic complete artifact.
- **External bindings become stale after target-key changes** → Validate both locators for every run
  and route stale/conflicting bindings to deterministic identity exceptions.
- **One production change spans control plane, ingestion, identity, and UI** → Deliver in vertical
  commits with contract tests at every boundary and keep later-provider code behind the registry.
- **A provider cannot distinguish students from teachers** → Reject unsupported entity selection
  unless an audited connection field rule supplies the distinction.
- **API feature flags accidentally affect old runs** → Select contract versions only at run
  creation and branch all v3 behavior on the persisted ingestion version.

## Migration Plan

1. Add additive connection, API-source, and external-binding tables and indexes.
2. Deploy provider registry, secret resolver boundary, and disabled API connector routes.
3. Deploy ingestion v3 role bindings and Graph v2 API resource dispatch behind a feature flag.
4. Enable synthetic DingTalk contract tests, then tenant-scoped DingTalk connections.
5. Enable synthetic WeCom contract tests and connections through the same registry.
6. Enable conversational provider selection after backend end-to-end verification.

Rollback disables new connection/task creation. Existing API runs keep their frozen versions and can
finish while the compatible v3 runtime remains deployed. Additive records are retained for audit;
rollback does not rewrite them into legacy entities.

## Open Questions

There are no blocking architecture questions. Provider sandbox credentials and the exact
tenant-visible custom fields used as school business numbers are deployment configuration, not
runtime inference.

# Reconciliation review fixes design

## Context

The first three reconciliation modules pass their existing tests, but review found four gaps:

- A task stops at `snapshots` and has no API entry point for entity resolution.
- Re-running entity resolution appends another automatic decision for every source entity.
- Multiple review decisions can reserve the same target without becoming duplicate conflicts.
- An incomplete field-mapping profile raises `KeyError` instead of a typed fatal ingestion issue.

The main worktree also contains uncommitted AI-analysis work. This change is isolated in
`fix/reconciliation-review-issues` and must not modify or absorb that work.

## Goals

- Make entity resolution reachable through the task API.
- Make completed entity resolution idempotent for the same task and snapshot pair.
- Enforce one-to-one target cardinality across accepted, review, and conflict decisions.
- Report missing required field mappings before inspecting mapped CSV columns.
- Preserve existing historical-mapping priority and append-only manual decision history.

## Non-goals

- Do not change AI mandatory-analysis code or its current tests.
- Do not add background workers, Celery, SSE, or automatic model-provider wiring.
- Do not combine entity resolution and difference detection into one operation.
- Do not rewrite existing historical difference or mapping records.

## API and workflow

Add `POST /api/reconciliation-tasks/{task_id}/resolve` with `ResolutionSummary` as its response.
The endpoint resolves the task's published authoritative and target snapshots and leaves the task
at `status=ready, stage=matching`. Clients can then call the existing difference-detection endpoint.

The route returns `404` when the task or snapshot pair does not exist and `409` when task state or
snapshot provenance makes resolution invalid. It uses the same session dependency and transaction
boundary as ingestion and difference routes.

```text
create task -> snapshots -> POST resolve -> matching -> POST differences/detect -> differences_ready
```

## Resolution idempotency

Resolution acquires a row lock on the reconciliation task before checking its stage. When the task
already has a complete decision set for the same source and target snapshots, the service rebuilds
and returns `ResolutionSummary` from the latest persisted decision per source entity. It does not
insert another automatic decision.

The decision set is complete only when every authoritative canonical entity has a persisted decision.
An incomplete set is not treated as a successful retry. Manual decisions remain append-only and the
latest decision continues to be authoritative for difference detection.

This service-level guard handles sequential retries and serializes concurrent PostgreSQL requests.
No uniqueness constraint is added because it would incorrectly block later manual decisions for the
same source entity.

## Target cardinality

Conflict resolution groups every target-bearing decision whose status is `accepted`,
`manual_review`, or `conflict`. When more than one source selects a target:

- one historical mapping remains accepted when it is the only historical decision;
- every competing non-historical decision becomes `conflict`;
- without a unique historical winner, every decision in the group becomes `conflict`.

Existing conflict decisions stay in the group so a later batch cannot silently accept another source
for an already contested target. Difference detection will materialize these decisions as explicit
duplicate conflicts using its existing logic.

## Required mapping validation

Before looking up mapped source-column names, ingestion checks that the profile defines all required
canonical mappings. Missing entries return fatal `IngestionIssue` values with code
`missing_required_mapping` and the canonical field name. Only after this check does validation report
`missing_required_column` for a configured mapping whose source column is absent from the CSV.

The required profile fields are the common row discriminators (`entity_type`, `source_id`, `name`)
and membership relationship fields (`member_source_id`, `container_source_id`, `role`). Optional
entity attributes remain optional.

## Testing

Each behavior is implemented test-first:

- API test proving a snapshot-ready task can be resolved and then detected.
- Service test proving two resolution calls return the same decisions without extra mapping rows.
- Conflict tests covering two manual-review decisions and a later decision joining an existing conflict.
- Ingestion tests distinguishing missing mapping configuration from a missing CSV column.

Final verification runs the complete backend suite, Ruff, mypy, frontend tests and build, and
`openspec validate demo`. PostgreSQL container verification is attempted only when Docker is available.

## Integration

Commit the fixes on `fix/reconciliation-review-issues`. After verification, merge the branch into
`master` without staging, reverting, or committing the unrelated AI-analysis work in the main
worktree. If Git reports an overlap with those uncommitted files, stop rather than stashing or
rewriting user work.

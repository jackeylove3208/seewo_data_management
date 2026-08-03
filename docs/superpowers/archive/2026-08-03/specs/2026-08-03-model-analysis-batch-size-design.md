# Model analysis batch size design

## Problem

Agent graph reconciliation currently accepts up to 50 actionable work items per model batch. The configured analysis batch size is not passed to `AgentBatchPlanner`, so large CSV and database differences can become one slow model request and exhaust four attempts at the 60-second timeout.

## Design

- Limit every reconciliation analysis model batch to at most 10 work items, regardless of source connector type.
- Pass `Settings.analysis_batch_size` through both the graph executor and the legacy CSV analysis worker into `AgentBatchPlanner`.
- Validate the configured analysis batch size as `1..10` and keep 10 as the default.
- Preserve the existing retry contract: one initial attempt plus three retries, for four total attempts.
- Keep historical completed batches immutable. Pending oversized batches are retained as `superseded` audit records and replaced by deterministic batches within the new limit before either runtime selects work.

## Verification

- Prove 43 same-entity work items partition as `10, 10, 10, 10, 3`.
- Prove `AgentBatchPlanner` persists batches using its configured limit.
- Prove an existing oversized pending batch is retained as superseded and repartitioned.
- Prove configuration rejects values above 10.
- Run focused batching, graph, configuration, lint, and type checks.

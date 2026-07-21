# Governance reporting and historical restore design

## Scope

Build on-demand, versioned HTML governance reports and an append-only historical restore workflow on top of the completed governance execution module. Reports are available for `succeeded` and `partial_failure` execution records. A restore selects a historical target version and creates one new compensation batch; it never rewinds or deletes history.

## Reporting

`ExecutionFactCollector` reads one immutable execution detail and stores a canonical `ExecutionFactBundle` plus SHA-256 hash on each report job. A new idempotency key creates the next report version; retrying the same key returns the original job. `ReportNarrativeGenerator` reuses `HttpLLMProvider`, the configured analysis model, retry policy, and task tokenization boundary through `generate-governance-report@1.0.0`. Invalid or unavailable AI output falls back to deterministic content. `HtmlReportRenderer` renders escaped, inspectable HTML stored with a content hash.

## Historical restore

Target versions remain append-only. A restore request records the physical current version, selected historical version, resolved semantic source state, covered version path, deterministic plan hash, AI candidate/provenance, operator, compensation plan/batch, and resulting output version.

The deterministic planner walks `TargetVersionRecord.parent_version_id` and successful `OperationAttemptRecord.target_version_id` facts. Moving backward swaps before/after and reverses dependency order; moving forward replays the original verified operations. When the current version was produced by a restore, its restore link identifies the historical semantic state used for the next path calculation.

AI reads the same intervening execution facts and any existing reports to propose operation references and explain impact. AI output is advisory: missing, invented, reordered, or altered operations are rejected against the deterministic plan. Model failure never removes deterministic restore capability.

Every restore is high risk. Confirmation binds the current target version and preview hash. Execution uses the existing batch executor and verifier. The output is accepted only when its content hash equals the selected historical target version. A later restore to another historical version creates another new batch and version.

## Frontend

Execution detail exposes report versions and a target-version timeline. Operators can generate/view/download HTML reports, select a historical restore point, inspect affected operations and conflicts, review AI explanation availability, acknowledge high risk, confirm, and monitor the resulting ordinary execution batch. The frontend never computes restore eligibility or operations.

## Failure semantics

Verification-failed or fact-incomplete operations block preview. A changed current version makes a preview stale. Failed report AI calls use deterministic fallback. Failed restore operations remain in the compensation batch and use ordinary eligible retry; history and restore links remain immutable.

## Testing

Cover append-only report versions, idempotency, historical facts after target drift, AI provenance and fallback, HTML escaping, backward and forward restore paths, repeated V3-to-V1 then V4-to-V2 restore, stale preview, uncertain outcomes, immutable originals, backend-owned actor identity, API failure states, and frontend confirmation gating.

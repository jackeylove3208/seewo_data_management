# Template-assisted analysis and failure reporting design

## Problem

Actionable reconciliation analysis currently sends every work item through the
model in batches. A recent mitigation reduced the default batch size from ten
items to five, but this doubled the number of model batches for large tasks.
CSV authority tasks are especially exposed because they can produce many
`target_extra` and `target_missing` work items that share the same decision
shape. One failed batch still blocks the whole analysis stage after four
semantic attempts.

The observed CSV run produced 66 actionable work items and 14 five-item model
batches. Five batches completed before a later student batch exhausted its four
attempts. The failed batch successfully checkpointed and replayed a read-only
identity tool, but its repaired model output still violated the strict finding
contract and later attempts encountered provider failures. Selecting only
students still leaves 48 actionable items, so reducing the requested entity
scope does not sufficiently reduce the model workload.

Termination reporting also loses the original cause. When an operator
terminates a run that is already blocked by a model failure, the deterministic
termination report currently records only `operator_requested`. The persisted
model failure, failed batch, attempt categories, and preserved investigation
progress do not reach the report.

## Goals

- Restore the default maximum analysis batch size to ten items.
- Analyze one representative with the model for each homogeneous,
  low-ambiguity `target_extra` or `target_missing` group.
- Reuse the validated model template for all other members of that group while
  retaining their individual facts, evidence references, and audit identity.
- Apply the same normalized eligibility rules to CSV, MySQL, HTTPS, DingTalk,
  and future connectors.
- Keep ambiguous identity work and field differences on the existing model
  analysis path.
- Persist specific, privacy-safe model provider and output-contract failure
  reasons.
- Produce a truthful failure analysis when an operator terminates an already
  blocked task.
- Preserve existing risk policy, approvals, execution authorization, school
  locks, replay protection, and privacy boundaries.

## Non-goals

- Copying one person's identifiers, values, or evidence references into
  another person's finding.
- Using connector names as a shortcut for template eligibility.
- Template reuse for `field_difference`, `identity_conflict`, or any work with
  unresolved identity candidates.
- Increasing the four-semantic-attempt limit for ordinary analysis.
- Letting a failure-report model call delay or block terminal reporting.
- Persisting raw prompts, raw model responses, credentials, or unmasked
  personnel data in failure audits.

## Design

### Connector-neutral template profiles

Template eligibility is calculated only after connector data has passed the
existing inspection, mapping, normalization, and matching stages. The policy
therefore operates on canonical work items and paired evidence rather than on
CSV, database, or API implementation details.

A template profile contains only decision-relevant structure:

- entity kind: department, student, or teacher;
- work kind: `target_extra` or `target_missing`;
- server-authorized operation set;
- authority-record and target-record presence shape;
- required and optional field-availability mask;
- identity posting kinds and identity-evidence state;
- candidate, claim, and clarification state;
- input-contract, operation-policy, and template-policy versions.

The profile hash excludes names, phone numbers, email addresses, identifiers,
locators, work-item IDs, and other personnel values. Templates are scoped to
one run even when two runs produce the same profile hash.

A work item is eligible only when all of the following hold:

- its kind is `target_extra` or `target_missing`;
- it has no candidate conflict, unresolved clarification, active identity
  claim, or competing allowed candidate;
- its input passed the existing contract and contains the fields required for
  every operation the template may select;
- its evidence state exactly matches the template profile;
- existing server policy can validate the selected operation and risk level.

Any uncertainty fails closed to the ordinary model-analysis path. Eligibility
does not weaken matching, identity, evidence, or operation validation.

### Representative template generation

The planner orders work deterministically by entity kind, work kind, canonical
input order, and work-item ID. The first eligible item in a profile is the
representative.

The representative is sent to a dedicated structured Skill that returns an
`analysis-template-v1` contract containing:

- a general Chinese category and explanation template;
- a recommended operation selected from the server-authorized set;
- risk level;
- a general solution template;
- the profile assumptions required for reuse.

The output must be general and must not contain representative-specific names,
identifiers, locators, or evidence references. The backend validates the
operation, risk, profile assumptions, absence of personnel values, and template
schema before reuse.

If template generation exhausts its four semantic attempts, the run pauses on
that profile through the existing blocked-model-error state. Completed batches
and templates from other profiles remain durable and are not recomputed after
the operator retries or terminates the task. Model-directed read-only tools
retain the existing checkpoint and replay behavior.

### Deterministic template instantiation

After validation, the backend instantiates one normal finding per work item.
Each finding receives its own:

- finding ID and work-item ID;
- persisted work kind and entity kind;
- task-local evidence references;
- operation constrained by that item's allowed operation set;
- risk and approval behavior from existing server policy;
- template provenance and representative invocation reference.

The general analysis and solution text may be shared, but personnel facts and
opaque evidence identity are always taken from the current work item. The
existing exact-coverage, disposition, evidence-membership, dependency, risk,
and operation validators run on every instantiated finding.

Template results are stored in the existing durable checkpoint facility under
a versioned key derived from run ID and profile hash. The payload contains the
validated template, profile, content hash, representative work-item reference,
and model invocation provenance. It contains no raw model conversation or
personnel values. Subsequent batches record deterministic template-reuse audit
provenance pointing to that checkpoint.

### Batch planning and persistence

The default `analysis_batch_size` returns to ten, which remains the hard model
contract limit. Batch planning becomes profile-aware:

- eligible items are grouped by template profile;
- ordinary items are grouped by entity kind and existing actionable kind;
- no batch contains more than ten items;
- a template group larger than ten is persisted in multiple model-batch audit
  units, but it still consumes only one representative model analysis;
- completed audit units are never recomputed after a later profile or batch
  fails.

This reduces model requests for repetitive work while keeping bounded
transaction, progress, and recovery units. Ordinary non-template batches may
contain up to ten items and continue to use the current structured finding
contract.

### Specific repair feedback

Generic `ValueError` repair feedback is replaced at the model boundary by
bounded, privacy-safe contract codes. At minimum, analysis output distinguishes:

- missing, duplicate, or unexpected work items;
- disposition mismatch;
- evidence reference missing or outside the manifest;
- operation outside the allowed set;
- risk or dependency conflict;
- malformed tool request or tool argument;
- schema validation failures.

Repair feedback includes safe counts and task-local opaque work-item references
when the model needs them to correct exact coverage. It never includes personnel
values. The next semantic attempt receives these specific codes after all
completed tool checkpoints have been reconstructed.

### Provider failure classification

The provider boundary maps failures to stable safe codes instead of collapsing
them all into `model_provider_failure`:

- `model_timeout`;
- `model_transport_failure`;
- `model_rate_limited`;
- `model_upstream_5xx`;
- `model_http_rejected`;
- `model_response_invalid_json`;
- `model_response_contract_missing`.

Attempt provenance records the safe code, bounded HTTP status class when
available, request duration, returned request ID, and token usage when a usable
response exists. The original exception text and response body are not stored.
Transport retries remain separate from semantic repair attempts so a transient
connection failure does not consume the same diagnostic meaning as an invalid
analysis result. Each pending model turn uses the existing configured
`model_retry_attempts` bound and backoff for timeout, transport, rate-limit, and
retryable 5xx failures. Exhausting that bounded transport loop fails the current
semantic attempt with its specific safe provider code.

### Failure-aware termination reporting

Report fact collection includes the latest persisted Agent failure and safe
progress facts:

- failure code, categories, failed graph node, and semantic attempt details;
- failed model batch and its entity/work profile;
- completed, pending, and blocked batch counts;
- completed tool checkpoint count and whether replay occurred;
- persisted provider timing and output-contract codes;
- recorded findings, succeeded mutations, and verified mutations.

Termination context distinguishes two cases:

1. `operator_requested`: the operator stopped an otherwise active task.
2. `system_failure_then_operator_terminated`: the task was already blocked by
   a persisted system or model failure and the operator chose to terminate it.

For the second case, a dedicated read-only failure-analysis Skill receives only
the safe fact bundle. It may produce a bounded narrative with the primary cause,
affected stage, preserved progress, confidence, and recommended operator action.
It has no business tools and cannot mutate task or connector data.

Failure narration is best effort and makes at most one provider request. It
cannot block terminal reporting. If the model remains unavailable or its output
is invalid, a deterministic renderer produces the same factual sections from
the safe codes. The report must never fall back to saying only that the operator
manually terminated the task when a prior failure exists.

## Impact on other connectors

MySQL, HTTPS, DingTalk, and CSV continue to use their existing connector,
mapping, normalization, matching, execution, and rollback implementations. The
shared change begins only after canonical actionable work exists.

Eligible repetitive work from any connector may use fewer model calls and may
share general explanatory text. Individual evidence, operation authorization,
risk classification, approvals, and execution behavior remain server-owned and
unchanged. Ambiguous or non-homogeneous work follows the existing model path.

Restoring ten-item ordinary batches can increase individual prompt and output
size, so output-budget tests and the existing strict ten-item limit remain
mandatory. Template groups offset this risk for the high-volume repetitive
cases that motivated the change.

Failure-aware reporting benefits every connector but changes only reports for
tasks with a persisted failure before termination. Normal successful reports
and ordinary operator-requested termination wording remain unchanged.

## Security and privacy

- Template profiles and hashes exclude personnel values.
- Representative output is rejected if it contains representative-specific
  identity values.
- Each instantiated finding uses only its own manifest-bound evidence.
- Existing authorization and high-risk approval gates remain mandatory.
- Failure facts contain safe codes, counts, timings, and opaque task-local IDs,
  not raw prompts or response bodies.
- Student phone values remain task-scoped tokens at every model boundary.
- Failure analysis has no mutation tools and cannot expand report facts.

## Testing

Focused automated tests cover:

- default and maximum analysis batch size are ten;
- one homogeneous eligible profile invokes the model exactly once;
- more than ten members persist in ten-item audit batches while reusing one
  template;
- deterministic representative selection and replay produce the same template
  checkpoint;
- CSV, database, and API fixtures use the same eligibility policy;
- candidate conflicts, claims, clarifications, field differences, and profile
  mismatches bypass template reuse;
- every instantiated finding exactly covers one work item and cites only that
  item's evidence;
- template operations, risk, and approvals cannot exceed server policy;
- interrupted tasks resume completed templates and completed batches;
- timeout, transport, rate-limit, 5xx, HTTP rejection, invalid JSON, and output
  contract failures persist distinct safe codes;
- repair feedback identifies coverage, disposition, evidence, and operation
  violations without personnel values;
- a blocked-model-error task terminated by an operator reports the original
  failure as its primary reason;
- ordinary manual termination still reports `operator_requested`;
- failure-analysis model success produces a validated narrative;
- failure-analysis model failure produces a truthful deterministic fallback;
- existing connector, graph analysis, governance, reporting, rollback, and
  privacy suites remain green.

## Rollout

The behavior applies to newly planned analysis batches after deployment.
Existing completed findings and terminal reports are not rewritten. Existing
blocked tasks retain their persisted failures and can use failure-aware
termination reporting when sufficient safe audit facts are available.

No new database table is required for template reuse or failure categories;
versioned checkpoint payloads and existing JSON provenance fields provide the
durable boundary. If implementation reveals a need for cross-run template
reuse, that is a separate design because it would require stronger privacy,
cache invalidation, and model-version controls.

# Resumable Agent tool investigation design

## Problem

The graph analysis sub-agent uses model-directed tools to investigate reconciliation evidence before returning a structured finding batch. One semantic attempt may therefore contain several real model requests: each tool selection is followed by another model request, and the last request produces the final structured result.

The current runner persists only terminal invocation facts. Its model and tool loop runs inside one long database transaction, and a later provider or output failure causes the next semantic attempt to rebuild the conversation from the initial prompt. Successfully completed tool investigation is therefore lost even though the tool calls themselves were authorized and completed.

The observed failure demonstrates the issue. One attempt completed four read-only tools and made five model requests, but its final output failed validation. The following attempts started without those tool results. The user-facing count of four attempts refers to four semantic attempts, not four real model requests.

## Goals

- Preserve the model-directed, step-by-step tool investigation workflow.
- Keep the existing maximum of four semantic attempts per sub-agent invocation.
- Resume after provider, output-contract, worker, or lease interruption without discarding completed tool investigation.
- Retry only the failed model turn or final structured-output step.
- Keep existing evidence, tenant, authorization, privacy, and school-lock boundaries.
- Avoid persisting raw sensitive personnel values as model conversation logs.

## Non-goals

- Increasing the semantic-attempt limit above four.
- Removing analysis tools or replacing the Agent investigation with deterministic analysis.
- Allowing tools outside the pinned Skill or graph-node boundary.
- Persisting complete prompts, raw model responses, or raw tool-result payloads.
- Changing reconciliation business rules, risk policy, or execution approval behavior.

## Design

### Durable investigation checkpoint

Each successful tool call creates a committed investigation checkpoint before another model request begins. The checkpoint records only replay-safe state:

- graph run, cursor, action, Skill, invocation, and semantic attempt identity;
- ordered conversation position and tool-call sequence;
- authorized tool name;
- validated replay descriptor containing only allowlisted resource IDs, evidence references, and bounded query parameters;
- arguments hash and result hash;
- trace ID, authorization result, and completion status;
- safe repair feedback and model provenance needed to continue.

Raw personnel values, complete prompts, complete model responses, credentials, and raw tool-result payloads are not added to the checkpoint.

The existing `agent_subagent_invocations` record remains the semantic-attempt audit record. Tool checkpoints extend the existing `agent_tool_calls` audit boundary with the replay-safe descriptor and the model-turn position needed for deterministic reconstruction. A schema migration adds the required nullable columns so existing records remain valid.

### Transaction boundaries

The runner must not keep a database transaction open across the entire model/tool loop. Each state change uses a short transaction:

1. Load or create the current semantic attempt and its committed checkpoints.
2. Reconstruct the next model request outside a transaction.
3. Call the model outside a transaction.
4. Validate the returned final result or tool request.
5. Execute an authorized tool using its normal bounded transaction.
6. Commit the replay-safe checkpoint before the next model request.

Terminal success or failure is also committed in a short transaction. This makes completed investigation visible after a worker crash or lease interruption.

### Conversation reconstruction

The initial prompt and bounded input are reconstructed from the immutable graph action, evidence manifest, persisted work items, and task snapshots. For every completed checkpoint, the runner:

1. rebuilds the assistant tool-request message from the validated replay descriptor;
2. re-executes the authorized read-only tool against the locked task snapshot;
3. compares the reconstructed result hash with the committed result hash;
4. appends the authorized tool result to the in-memory conversation only when the hash matches.

Re-executing a local read-only tool is acceptable because it avoids persisting sensitive result payloads and does not consume a model request. If the hash differs, the invocation fails closed with an evidence-replay conflict instead of mixing evidence versions.

Submission or validation tools retain their existing idempotency boundary. Their replay descriptor contains only the already-validated, privacy-safe submission contract required to reproduce the same idempotent call.

### Failure and retry behavior

The maximum remains four semantic attempts.

- Provider or transport failure: finalize the current semantic attempt with a safe provider category. The next attempt reconstructs all completed tool checkpoints and retries the same pending model turn.
- Structured-output or contract failure: retain completed tool checkpoints, append safe validation feedback, and have the next attempt retry the final or current model turn without restarting investigation.
- Worker or lease interruption: mark the interrupted attempt safely, reclaim through the existing durable worker path, reconstruct committed checkpoints, and continue under the next allowed semantic attempt.
- Tool authorization failure: stop immediately under the existing fail-closed rule; it is not made retryable by checkpointing.
- Tool replay hash mismatch: stop with a dedicated safe evidence-replay conflict.
- Fourth semantic attempt failure: preserve the existing blocked-model-error transition, task data, and school lock behavior.

Attempt details continue to report semantic attempts. Model request and tool-call counts remain separate provenance fields so the UI and operators do not confuse the two.

### Idempotency and concurrency

Checkpoint identity is unique for graph run, cursor, action, Skill, and ordered tool sequence. Replaying the same descriptor and result hash returns the existing checkpoint. A different descriptor or result hash at the same position raises a graph fact conflict.

All continuation writes remain fenced by worker ID, run lease token, graph cursor, and semantic attempt. A stale worker cannot append checkpoints or finalize an invocation after another worker has reclaimed the run.

### Security and privacy

- Replay descriptors use allowlisted structured fields, never arbitrary model text.
- Resource IDs and evidence references must belong to the current evidence manifest.
- Tool authorization is repeated during reconstruction.
- Only privacy-safe hashes and bounded descriptors are persisted.
- Student phone values remain task-scoped tokens at the model boundary.
- No new credentials, connector payloads, or unmasked personnel exports are stored.

## User-visible behavior

Normal successful workflows remain unchanged. A transient failure after several tools may take another semantic attempt, but the Agent continues from the accumulated investigation rather than visibly starting over.

Failure presentation must distinguish semantic attempts from real model requests and tool calls. Existing blocked-state safety remains unchanged; wording may use the persisted failure category rather than describing every mixed failure as a structured-output error.

## Testing

Focused automated tests cover:

- a provider failure after two successful tools resumes with both tool results;
- a final contract failure retries final generation without repeating model-directed investigation;
- worker interruption resumes from committed checkpoints;
- reconstructed tool output must match its persisted result hash;
- replay of the same checkpoint is idempotent;
- stale leases cannot append or finalize checkpoints;
- unauthorized tools remain non-retryable;
- four semantic attempts still exhaust the invocation;
- model request counts and semantic attempt counts remain distinct;
- checkpoint payloads do not contain raw sensitive personnel values;
- existing graph analysis, reporting, rollback, and worker suites remain green.

## Rollout

The migration is additive and nullable for existing audit rows. The behavior applies to new and resumed graph sub-agent invocations after deployment. Existing terminal invocations are not rewritten. Operational verification should exercise a synthetic batch where a model provider failure is injected after successful read-only tool calls, then confirm continuation from the stored checkpoints.

# Reduce large model output failures

## Goal

Reduce structured-output failures that occur more often when an Agent task contains many changes,
without introducing adaptive batch trees, new persistence models, or additional user-interface
work.

## Root cause and scope

The model analysis path currently allows ten work items per batch. Larger batches require the model
to preserve more IDs, fields, enum values, and nested result objects in one response, increasing the
chance that strict server validation rejects an otherwise usable analysis.

The HTTP model request also leaves the output-token limit entirely to the gateway default. If that
default is lower than the structured response needs, a large response can be truncated or otherwise
returned incomplete. An explicit output limit is a safety bound, not a substitute for smaller
batches: it cannot repair semantically invalid or schema-incompatible output.

This change addresses those two bounded causes only. It does not add failure dashboards, dynamic
batch splitting, new database audit fields, or automatic response-mode negotiation.

## Configuration changes

- Change the default `analysis_batch_size` from `10` to `5`. Keep the existing configurable range
  and the hard maximum of `10`, so an operator can still tune synthetic or known-safe deployments.
- Add `llm_max_output_tokens` as a positive integer setting with a default of `8192`.
- Include `max_tokens: llm_max_output_tokens` in every OpenAI-compatible chat-completions request.
- Preserve `llm_extra_body_json` as the final provider-specific override. Deployments that require a
  different output limit or parameter behavior can override the generated value explicitly.

The existing `llm_timeout_seconds=120`, four-attempt outer repair policy, `json_object` response
mode, schema prompt, response example, and safe validation feedback remain unchanged.

## Runtime behavior

New analysis work is created in batches of at most five items by default. Existing persisted batches
are not rewritten. A failed structured response follows the current bounded repair flow, but each
new default batch contains half as many work items and therefore needs a smaller structured result.

Every model request carries an explicit maximum of 8192 output tokens. The model may return fewer
tokens. The setting does not reserve tokens, increase the model context window, or guarantee schema
validity.

## Compatibility and safety

The change is configuration-compatible and requires no migration. Existing environment variables
continue to work. `LLM_MAX_OUTPUT_TOKENS` can override the default, and an explicitly supplied
`llm_extra_body_json.max_tokens` remains authoritative for provider compatibility.

No raw model output, credentials, personal data, or new telemetry is persisted. Task locking,
idempotency, retries, and fail-closed validation remain unchanged.

## Testing

Automated coverage will verify:

- the default analysis batch size is five and values up to ten remain valid;
- analysis batch creation respects the new default through existing settings wiring;
- the default model request body contains `max_tokens: 8192` in both JSON response modes;
- `LLM_MAX_OUTPUT_TOKENS` changes the request limit;
- `llm_extra_body_json.max_tokens` can override the standard value;
- timeout, retry, structured-response parsing, and existing provider request fields remain intact;
- backend tests, Ruff, and mypy pass.

## Out of scope

This change does not prove whether a historical failure was truncated because the gateway does not
currently retain `finish_reason`. It does not guarantee that every model response passes business
validation. If large batches still fail after this change, the next scoped improvement is adaptive
batch splitting based specifically on `model_contract_failure` and `model_output_failure`.

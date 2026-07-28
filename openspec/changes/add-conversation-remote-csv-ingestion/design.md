## Context

The current conversation endpoint persists a user message, builds the complete conversation
context, and asks `converse-school-data-sync@1.0.0` to select server-listed local CSV or SQL
connectors. `AgentTaskService` binds both task sources before `agent-sync-graph-v1` enters
`inspect_sources`. Model-facing graph tools already reject `url`, `path`, `dsn`, `sql`, and
credentials and authorize resources through a frozen evidence manifest.

Remote CSV input crosses conversation processing, persistence, task creation, graph execution,
network security, file storage, and model evidence. The manual-sync API must remain upload- or
configuration-based, and historical graph runs must retain their original transition vocabulary.

## Goals / Non-Goals

**Goals:**

- Activate remote CSV ingestion only from a link in an authenticated Agent conversation message.
- Keep the complete URL outside model requests, MCP calls, client errors, reports, and displayed
  persisted chat text.
- Materialize a public HTTPS CSV once as an immutable authoritative task snapshot.
- Reuse deterministic CSV inspection and normalization, invoking a Skill only for ambiguous
  fixed-six-field semantics.
- Preserve task idempotency, worker recovery, school locks, evidence manifests, and historical
  `agent-sync-graph-v1` behavior.

**Non-Goals:**

- Manual-sync link input or manual `remote_csv` task creation.
- HTML scraping, browser automation, authenticated downloads, cookies, custom headers, APIs,
  HTTP/FTP, Excel, JSON, or archives.
- Scheduled refresh or automatic replacement of a running task snapshot.
- Model-controlled networking or changes to the three-entity six-field contract.

## Decisions

### Detect and register links inside conversation message processing

Add a focused remote-source service used only by
`POST /api/agent/conversations/{conversation_id}/messages`. It extracts at most one HTTP(S) URL,
validates registration-level syntax, creates a tenant/operator/conversation-bound record, and
replaces the URL with `[远程CSV来源:<clean-origin>]` before appending the user message and building
model history. A normal message creates no record. Invalid or multiple links return a deterministic
clarification without calling the model.

`ConversationAgentContext` receives trusted remote-source summaries, and
`ConversationAgentDecision` may select one `remote_source_id`. Backend reference validation accepts
only a resource listed for the current conversation.

Alternative considered: expose `POST /remote-sources` and let either UI register a link. This was
rejected because it expands manual-sync scope and makes the conversation-only authorization rule
dependent on client behavior.

### Enforce conversation-only use below the API layer

Add `remote_csv` and `remote_source_id` to the internal Agent connector contract, but require a
matching non-null `conversation_id` in `AgentTaskService.create`. The manual `/api/agent/tasks`
route continues to reject every non-CSV upload pair before creating a task or school lock. Starting
a conversation task atomically verifies and binds the remote record to that task.

Alternative considered: hide a remote option only in React. This was rejected because a forged API
request would still activate the capability.

### Persist remote-source lifecycle separately from immutable files

Create `RemoteSourceRecord` with tenant, creator, conversation, optional task and `SourceFile`,
original URL, cleaned origin, state, content hash, size, media type, retrieval time, and safe problem
code. The URL column is treated as sensitive server data and is never projected by public schemas.
The record owns the mutable lifecycle (`registered`, `materializing`, `ready`, `failed`);
`SourceFile` and `Snapshot` remain the immutable published content.

The target local CSV is bound at task creation. The authoritative `SourceFile` and `Snapshot` are
created only after a complete valid download, so downstream source queries cannot see a partial
file.

Alternative considered: create a placeholder `SourceFile` containing the URL. This was rejected
because `SourceFile` is used as readable evidence and would blur network authority with immutable
file provenance.

### Use `agent-sync-graph-v2` within the existing durable workflow

Remote tasks keep workflow version `agent-graph-v1` but select persisted graph version
`agent-sync-graph-v2`. The graph adds:

```text
acquire_school_lock -> materialize_sources -> inspect_sources
```

`materialize_sources` offers only `materialize_remote_authority`. On success it transitions to
`inspect_sources`. Existing local/upload/SQL tasks continue to start with `agent-sync-graph-v1`;
rollback remains `agent-rollback-graph-v1`. Candidate-template selection must distinguish sync v1,
sync v2, and rollback explicitly instead of treating every non-v1 graph as rollback.

Alternative considered: insert a new action into the existing `inspect_sources` node. This was
rejected because it changes the replay meaning of historical node/cursor/action combinations.

### Pin the actual network connection to approved public addresses

Implement a dedicated downloader with injectable DNS resolver and HTTP transport. The resolver
rejects IP literals and every non-global address. The production transport resolves each host,
selects an approved address for the TCP connection, retains the original hostname for TLS/SNI and
Host validation, disables environment proxies, and returns redirects to the policy layer rather
than following them automatically. Every redirect is parsed and revalidated, HTTPS downgrade is
rejected, and the chain is capped at three.

The downloader uses the existing `max_upload_bytes`, explicit connect/read/total timeouts, and
streaming SHA-256 storage under a managed remote-source directory. It checks declared length and
actual streamed bytes, removes temporary files on all failures, rejects unsupported content, then
calls the existing deterministic CSV inspector before publishing.

Alternative considered: DNS pre-check followed by a normal hostname request. This was rejected
because resolution can change between validation and connection.

### Reuse CSV mapping contracts with a remote-specific Skill

Known headers remain fully deterministic. When a remote task needs ambiguous mapping,
`ProductionGraphActionExecutor` invokes
`understand-remote-organization-source@1.0.0` with the existing `CsvSchemaMappingInput` and
`CsvSchemaMappingOutput` contracts. The mapping action manifest includes the source pair and bounded
first-page resources. The Skill may call `inspect_configured_source` and `read_connector_page`, both
of which resolve only the materialized `SourceFile`; it can never request a URL.

The backend keeps the existing reference, uniqueness, entity applicability, normalizer, and
unresolved-field validation before freezing the mapping checkpoint.

Alternative considered: let the Skill identify encoding, format, and validity. This was rejected
because deterministic parsing provides stronger, cheaper, and reproducible evidence.

### Keep frontend changes presentation-only

The composer continues sending ordinary text through the existing API. No URL parsing, registration,
or security decision occurs in React. The UI renders the backend-sanitized user message and can show
the cleaned remote origin in the existing intent/confirmation presentation. No manual-sync component
or API type gains remote controls.

## Risks / Trade-offs

- [Public sites can be slow or unstable] → Bound time and size, classify retryable transport
  failures, and never block the conversation request on download.
- [DNS rebinding or redirect-based SSRF] → Pin each connection to a resolver-approved public address
  and re-run policy for every redirect.
- [A URL query may contain a secret] → Store it only in the private record and replace it before
  model, chat display, logs, errors, and reports.
- [A crash can leave a complete file without a transition] → Use an action idempotency key and
  atomic database publication; recovery reuses complete hashed content and removes temporary files.
- [Remote CSV cells can contain prompt injection] → Treat rows as untrusted evidence, tokenize
  protected values, bound pages to fifty, and keep fixed output schemas and tool allowlists.
- [A new graph version increases routing complexity] → Persist graph version per run and add
  exhaustive selection tests for sync v1, sync v2, and rollback.

## Migration Plan

1. Add the remote-source table and indexes with nullable task/file bindings.
2. Deploy code with conversation remote-source activation disabled by a new server setting.
3. Run migration and regression suites, including historical graph restoration and manual rejection.
4. Enable the setting together with the Graph worker capable of `agent-sync-graph-v2`.
5. Roll back by disabling new registrations. Existing registered or active remote tasks remain
   readable by the deployed graph v2 worker until terminal; the migration is not destructively
   downgraded while records are referenced.

## Open Questions

None. The first release is deliberately limited to one public HTTPS direct CSV link per conversation
message and one conversation-bound remote authoritative source per task.

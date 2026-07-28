# Connector contract

Downstream reconciliation code reads canonical entities through `SourceConnector` and does not read CSV files directly. `TargetConnector` adds mutation and verification methods for governance execution.

The implementation provides:

- `ThirdPartyCsvConnector` for authoritative CSV data.
- `MofaCsvConnector` for Seewo/Mofa target CSV data; mutation is intentionally deferred to the governance-execution module.
- capability-gated configured HTTP JSON and SQLAlchemy stores with server-owned configuration,
  credential references, bounded stable pagination, schema/health discovery, optimistic
  versions, idempotency keys, allow-listed mutations, and read-after-write verification;
- `ThirdPartyApiConnector`, `SeewoApiConnector`, and `DatabaseSourceConnector` façades over those
  configured stores.

The durable `new-agent-v1` worker currently binds CSV end-to-end. Agent task submission rejects
an API/database selection with the stable `connector_capability_failure` code before it creates a
task or acquires a school lock. This is intentional until configured records can be materialized
as immutable Agent evidence and the connector target session can be reconstructed safely after a
worker restart.

Configured connectors must preserve the canonical Pydantic models and provenance. Authentication
or DSN values remain behind server-side credential references; clients and model payloads receive
only configuration IDs. Pagination must be bounded and stably ordered. Every target write needs a
stable version token, idempotency key, allow-listed operation, optimistic conflict check, and
read-after-write verification. Credentials must never enter snapshots, events, reports, or logs.

## Conversation remote CSV source

An authenticated Agent conversation can register one public HTTPS CSV link from a user message.
This is the only remote-link entry point. The browser receives and displays only a cleaned origin,
such as `[远程CSV来源:data.example.test]`, plus a server-issued `remote_source_id`; the full URL
and its query string remain in the private remote-source record.

Registration does not make a network request. After the user confirms task start,
`agent-sync-graph-v2` materializes the authoritative file once, validates it, hashes it, and binds
the immutable `SourceFile` snapshot to the task. Retries reuse that snapshot even if the URL later
returns different content.

The downloader accepts direct CSV content only and enforces:

- HTTPS with a public domain; credentials, fragments, IP literals, private/loopback/link-local,
  multicast, reserved, and metadata destinations are rejected;
- validation and address pinning on every redirect, with at most
  `RECONCILIATION_REMOTE_SOURCE_MAX_REDIRECTS` redirects (default `3`);
- connect, read, and total timeouts of `10`, `30`, and `60` seconds by default;
- the existing `RECONCILIATION_MAX_UPLOAD_BYTES` limit (default `50 MiB`);
- deterministic rejection of empty or malformed CSV, HTML, JSON, spreadsheets, and archives.

Safe task failures use stable codes in these groups: DNS/policy, redirect, timeout/transport, HTTP
status, size, content type, and CSV parsing. Client responses, model evidence, reports, and normal
logs never include the full URL, query string, response body, or raw protected row values.

The manual task endpoint remains upload/local-source only. It rejects `remote_csv`, a URL, or a
`remote_source_id` before creating a task, run, source binding, or school lock. The manual-sync UI
has no remote-link field or remote-source option.

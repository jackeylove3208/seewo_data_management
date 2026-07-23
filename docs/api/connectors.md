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

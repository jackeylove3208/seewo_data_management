# Connector contract

Downstream reconciliation code reads canonical entities through `SourceConnector` and does not read CSV files directly. `TargetConnector` adds mutation and verification methods for governance execution.

The current implementation provides:

- `ThirdPartyCsvConnector` for authoritative CSV data.
- `MofaCsvConnector` for Seewo/Mofa target CSV data; mutation is intentionally deferred to the governance-execution module.
- `ThirdPartyApiConnector` and `SeewoApiConnector` that fail with `ConnectorNotConfigured` until real contracts and credentials exist.
- `DatabaseSourceConnector` as the explicit boundary for the planned database input.

Future API and database connectors must preserve the same canonical Pydantic models and provenance. They must define authentication or DSN handling, bounded pagination, rate limits, a stable source-version token, retry behavior, mutation idempotency, and read-after-write verification. Credentials belong in environment-backed settings and must never enter snapshots or logs.

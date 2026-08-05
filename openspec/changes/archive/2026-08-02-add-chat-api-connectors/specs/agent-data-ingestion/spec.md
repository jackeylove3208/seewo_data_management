## ADDED Requirements

### Requirement: Route mixed connectors through frozen source-role bindings
For `source-ingestion-v3`, the system SHALL freeze and independently route authoritative and target
source bindings for inspection, mapping, normalization, checkpointing, and replay.

#### Scenario: API authority reconciles against MySQL
- **WHEN** a task selects an active API authority connection and a configured MySQL target
- **THEN** authoritative actions use the API materialized snapshot and target actions use the
  database connector without reducing the task to one pair mode

#### Scenario: Role checkpoint is replayed
- **WHEN** a worker resumes ingestion v3
- **THEN** each role reuses only its own connector, mapping, source version, and normalization
  checkpoint

### Requirement: Normalize frozen API evidence directly to Agent inputs
The system SHALL deterministically project each selected frozen API entity to
`AgentContractRecord`, persist it as `AgentInputRecord`, and persist associated input marks without
creating legacy raw-row, canonical-entity, or entity-mapping records.

#### Scenario: API record is normalized
- **WHEN** a frozen provider record has a supported entity kind and valid stable external ID
- **THEN** the Agent input preserves task, run, snapshot, tenant, source role, deterministic locator
  and order, six-field projection, and input hash

#### Scenario: Stable locator replays with changed content
- **WHEN** the same run and API stable locator is replayed with a different projected input hash
- **THEN** ingestion raises a replay conflict and does not replace the prior Agent input

## MODIFIED Requirements

### Requirement: Inspect configured CSV, API, and database sources through connectors
For model-mediated ingestion v1, the ingestion sub-agent SHALL use only configured connector tools.
For source-ingestion v2 and v3, known CSV, API, and database sources SHALL be inspected by
deterministic backend code using frozen role bindings, versions, schemas, pagination evidence, and
supported capabilities without exposing raw credentials or arbitrary filesystem, URL, query, or
DSN access to a model.

#### Scenario: Configured CSV pair is submitted
- **WHEN** a task supplies readable third-party and Seewo CSV files
- **THEN** the existing file/CSV foundation creates immutable source-role snapshots under the run's
  frozen ingestion contract

#### Scenario: Configured API authority and database target are submitted
- **WHEN** an ingestion-v3 task has a complete API materialization and a configured target database
- **THEN** deterministic role-specific inspection records safe capability, snapshot, schema, and
  version checkpoints with zero API inspection model calls

#### Scenario: Connector is an unconfigured placeholder
- **WHEN** an API or database connector lacks required endpoint policy, role, version,
  authentication reference, or capability configuration
- **THEN** the task records a connector-configuration data error and does not claim that source was
  read

### Requirement: Apply strict authoritative completeness without mutating authority
The system SHALL validate authority according to its frozen ingestion contract and SHALL never
propose or perform a third-party write. Source-ingestion v1/v2 completeness behavior remains
unchanged. For source-ingestion v3 API authority, stable locator, entity kind, and name are required;
ordinary identity eligibility requires number, phone, email, or a valid external identity binding;
provider-unavailable ordinary fields SHALL be marked and excluded from governed comparison rather
than interpreted as empty.

#### Scenario: One authoritative student lacks class
- **WHEN** otherwise readable authoritative data contains that incomplete row
- **THEN** the row is marked and invisible to identity work, creates a mandatory AI anomaly analysis, is counted in the report, and cannot produce a third-party mutation

#### Scenario: API phone is unavailable
- **WHEN** a readable API authority record has a usable identity key but phone is hidden by provider
  permission
- **THEN** the record remains eligible, phone is marked `authority_field_unavailable`, phone is
  omitted from ordinary differences, and no third-party mutation is possible

#### Scenario: API authority has no identity evidence
- **WHEN** an API authority record has no number, phone, email, or valid external binding
- **THEN** the identity stage marks `authority_identity_absent`, creates a mandatory
  `authority_invalid` work item, and does not ask a model to infer identity

#### Scenario: Entire schema cannot be mapped
- **WHEN** either source role cannot supply its minimum frozen input contract
- **THEN** the supervisor skips reconciliation and governance and advances to a data-error report

### Requirement: Persist marked records and ingestion diagnostics
The system SHALL persist source locator, stable input order, reason code, affected fields, sanitized
evidence, inclusion state, and report disposition for every marked row. CSV order SHALL use physical
row number, API order SHALL use provider entity precedence plus encoded stable external ID, and
database order SHALL use a configured stable primary key; a connector that cannot replay the same
locator and order SHALL fail as a data error.

#### Scenario: Report is generated
- **WHEN** the task reaches any terminal report state
- **THEN** report facts include marked authoritative and target counts, unavailable-field counts,
  identity-absent counts, and safe reasons without exposing raw student phone or provider secrets

# Agent data ingestion Specification

## Purpose

Define connector-backed, immutable ingestion and normalization for new Agent tasks.
## Requirements
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

### Requirement: Normalize new tasks to the three-entity six-field contract
The system SHALL normalize new Agent tasks to department, student, and teacher records with exactly
the fixed fields `category`, `name`, `number`, `class_name`, `phone`, and `email`; it SHALL reject
invented or extra mapping keys, and SHALL treat `class_name` as applicable only to students.

#### Scenario: Student row is normalized
- **WHEN** an inspected row is identified as a student
- **THEN** its canonical Agent payload contains only `category`, `name`, `number`, `class_name`,
  `phone`, and `email`, plus the separately defined source locator, source role, and raw-row
  provenance fields

#### Scenario: Mapping invents an output key
- **WHEN** a deterministic or model-produced mapping includes a key outside `category`, `name`,
  `number`, `class_name`, `phone`, and `email`
- **THEN** the backend rejects the mapping and does not create normalized input records

#### Scenario: Historical task is read
- **WHEN** an existing task contains legacy class or membership entity records
- **THEN** historical APIs continue to decode them without routing them into the new Agent contract

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

### Requirement: Preserve Seewo rows for downstream evidence without input-time mutation
The ingestion sub-agent SHALL NOT delete or update a Seewo row, SHALL treat non-empty number, phone, and email as identity candidate keys, and SHALL allow category, class, or name to be absent for downstream analysis.

#### Scenario: Seewo row has only an email
- **WHEN** category, class, name, number, and phone are empty but email is present
- **THEN** the row remains eligible for identity lookup and ordinary field completion

#### Scenario: Seewo row has no identity-key value
- **WHEN** number, phone, and email are all empty
- **THEN** the row remains immutable input evidence and is routed to target-extra analysis rather than deleted during ingestion

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

### Requirement: Ingest a materialized remote CSV as authoritative data
The ingestion runtime SHALL treat a successfully materialized remote CSV `SourceFile` as a
read-only authoritative CSV snapshot and SHALL reuse the existing deterministic CSV inspection,
fixed three-entity six-field contract, normalization, marking, and stable row provenance.

#### Scenario: Remote CSV uses known field names
- **WHEN** deterministic aliases uniquely map the remote CSV to the fixed contract
- **THEN** ingestion freezes the mapping and normalizes the file without a semantic model call

#### Scenario: Remote CSV has ambiguous organization fields
- **WHEN** the CSV is structurally valid but fixed rules cannot uniquely map required business fields
- **THEN** a versioned source-understanding Skill receives bounded profile evidence and returns only fixed-contract mappings for backend validation

#### Scenario: Skill mapping is invalid
- **WHEN** the Skill invents a field reference, entity type, contract field, normalizer, or evidence reference
- **THEN** the backend rejects the mapping and does not create normalized input records

### Requirement: Bound model-mediated schema mapping
Before mapping a configured database schema, the system SHALL provide the model only a bounded,
sanitized schema-metadata envelope whose serialized UTF-8 representation does not exceed 256 KiB,
and SHALL NOT provide raw rows, credentials, arbitrary SQL, generic table access, or unbounded
evidence. The model result SHALL use exactly the six fixed output fields `category`, `name`,
`number`, `class_name`, `phone`, and `email`; invented or extra keys SHALL be rejected.

#### Scenario: Model receives schema metadata before mapping
- **WHEN** an LLM-mode connector requires semantic mapping
- **THEN** the model receives only bounded metadata such as table/column names, types, nullability,
  and bounded relationship metadata, with no raw row values or credential material

#### Scenario: Model requests prohibited access
- **WHEN** a model request attempts arbitrary SQL, generic table access, raw-row retrieval, credential
  access, or evidence beyond the configured metadata bound
- **THEN** the model boundary denies the request, records a safe mapping error, and does not expand
  the evidence supplied to the model

#### Scenario: Schema metadata exceeds the bounded envelope
- **WHEN** the serialized schema-mapping input would exceed 256 KiB
- **THEN** the backend rejects the input before invoking the model and records a safe mapping error

#### Scenario: Model returns an invented field
- **WHEN** a model mapping result contains a key other than `category`, `name`, `number`,
  `class_name`, `phone`, or `email`
- **THEN** the backend rejects the result and does not persist or apply the mapping

### Requirement: Resume historical tasks from frozen mapping contracts
The system SHALL resume historical tasks using their persisted `workflow_version`, `graph_version`,
frozen source bindings, mapping checkpoint keys, and mapping checkpoint results. Changes to current
connector configuration SHALL affect only tasks created after the change and SHALL NOT rewrite the
persisted contract of an existing task or run.

#### Scenario: Historical task resumes after configuration changes
- **WHEN** a task created under one connector configuration is resumed after that configuration is
  edited
- **THEN** the worker uses the persisted workflow and graph versions, frozen source bindings, and
  mapping checkpoint keys/results from that task rather than rereading current configuration

#### Scenario: New task sees current configuration
- **WHEN** a new task is created after an LLM mapping configuration changes
- **THEN** the new task freezes the current validated configuration and its own mapping checkpoint
  contract without altering historical tasks

### Requirement: Load strict database connector configuration from YAML
The system SHALL expose an optional database connector configuration file, parse it with safe YAML
loading, reject non-object roots, unknown keys, malformed entries, and duplicate connector IDs, and
merge valid file definitions with the legacy environment JSON map into
`Settings.database_connector_configurations`.

#### Scenario: YAML defines an LLM target connector
- **WHEN** the configured YAML file contains a target connector with `mapping.mode: llm` and valid
  credentials, dialect, table, key, version, role, and capabilities
- **THEN** settings loads the connector, exposes `mapping.mode == "llm"`, and normalizes absent
  `field_columns` and `allowed_columns` to `{}` and `()` respectively

#### Scenario: YAML root is invalid
- **WHEN** the YAML file is malformed or its root is not an object containing connector definitions
- **THEN** settings fails closed with a configuration validation error and does not expose a partial
  runtime map

#### Scenario: YAML and environment IDs collide
- **WHEN** the same connector ID is defined in both the YAML file and the legacy environment JSON
- **THEN** settings rejects the configuration rather than silently choosing one definition

#### Scenario: Legacy environment JSON is used
- **WHEN** no YAML connector file is configured and a valid legacy environment JSON map is present
- **THEN** settings loads the legacy definitions into the merged runtime map unchanged

### Requirement: Validate database connector mapping modes
The system SHALL support `explicit` and `llm` database mapping modes. Explicit target mappings SHALL
require a complete physical field mapping and allow-list, while LLM target mappings SHALL require
neither physical field columns nor an allow-list and SHALL remain eligible for later governed schema
mapping.

#### Scenario: Explicit target is incomplete
- **WHEN** a target connector selects `mapping.mode: explicit` but omits required physical mapping
  fields or its allowed-column list
- **THEN** settings rejects the connector as invalid configuration

#### Scenario: LLM target omits physical mapping
- **WHEN** a target connector selects `mapping.mode: llm` without `field_columns` or
  `allowed_columns`
- **THEN** settings accepts the connector with empty normalized mapping values and does not invent a
  physical schema mapping

#### Scenario: Unknown mapping mode is supplied
- **WHEN** a connector supplies a mapping mode other than `explicit` or `llm`
- **THEN** settings rejects the connector before ingestion can inspect it

### Requirement: Resolve and protect connector configuration paths and credentials
The system SHALL expose `Settings.database_connector_config_file` as an optional path, resolve a
relative configured path against `backend/`, and load only credential references through the existing
credential configuration without exposing raw credentials to ingestion or model-visible state.

#### Scenario: Relative YAML path is configured
- **WHEN** settings receives a relative database connector YAML path
- **THEN** it resolves the path from the `backend/` base before loading it

#### Scenario: Credential reference is unresolved
- **WHEN** a YAML connector references a credential that is absent from configured credentials
- **THEN** settings rejects the YAML-backed configuration during loading and does not make a usable
  connector available to ingestion, even when SQL execution flags are disabled

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

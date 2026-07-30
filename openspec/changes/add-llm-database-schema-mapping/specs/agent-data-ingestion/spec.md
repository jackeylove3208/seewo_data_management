## ADDED Requirements

## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Bound model-mediated schema mapping
Before mapping a configured database schema, the system SHALL provide the model only a bounded,
sanitized schema-metadata envelope and SHALL NOT provide raw rows, credentials, arbitrary SQL,
generic table access, or unbounded evidence. The model result SHALL use exactly the six fixed output
fields `category`, `name`, `number`, `class_name`, `phone`, and `email`; invented or extra keys SHALL
be rejected.

#### Scenario: Model receives schema metadata before mapping
- **WHEN** an LLM-mode connector requires semantic mapping
- **THEN** the model receives only bounded metadata such as table/column names, types, nullability,
  and bounded relationship metadata, with no raw row values or credential material

#### Scenario: Model requests prohibited access
- **WHEN** a model request attempts arbitrary SQL, generic table access, raw-row retrieval, credential
  access, or evidence beyond the configured metadata bound
- **THEN** the model boundary denies the request, records a safe mapping error, and does not expand
  the evidence supplied to the model

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
- **THEN** settings rejects the connector configuration and does not make a usable connector
  available to ingestion

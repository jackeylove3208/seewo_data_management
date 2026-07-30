## ADDED Requirements

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

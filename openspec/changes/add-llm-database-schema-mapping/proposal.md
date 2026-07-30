## Why

Database connector configuration currently requires a physical field mapping and allow-list even
when the source schema must be discovered and mapped by the LLM-assisted ingestion workflow. This
blocks safe onboarding of compatible databases whose columns are not known ahead of time and leaves
configuration split between an environment-only JSON map and ad hoc runtime assumptions.

## What Changes

- Add an optional YAML database-connector configuration file with strict, typed validation.
- Add an explicit mapping mode so target connectors can opt into `llm` schema mapping without
  declaring physical field columns or an allow-list.
- Merge YAML connector definitions with the legacy environment JSON map while rejecting duplicate
  connector IDs instead of silently overriding one source.
- Resolve relative configuration-file paths against `backend/`, fail closed for malformed YAML or
  invalid configuration roots, and preserve sanitized credential-reference resolution.
- Preserve explicit-mode requirements and the merged `Settings.database_connector_configurations`
  runtime map.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `agent-data-ingestion`: Permit configured database targets to use an explicit or LLM mapping mode,
  with strict connector configuration and safe schema-mapping behavior.

## Impact

- Backend settings and database connector configuration loading, including a new PyYAML runtime
  dependency.
- Database ingestion configuration consumed by connector inspection and schema-mapping workflows.
- Existing environment JSON configuration remains supported; duplicate IDs become configuration
  errors rather than precedence-based overrides.

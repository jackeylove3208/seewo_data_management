# LLM database schema mapping design

## Goal

Make configured SQL connectors readable and reusable across organization-data tables without
declaring physical six-field mappings or column allow-lists in one-line environment JSON. A
configured table remains server-selected, while the ingestion Skill derives and freezes the
physical mapping from reflected schema metadata.

Add a second Seewo MySQL target connector named `seewo-data-mysql` for
`seewo_data.data` without replacing the existing `seewo-mysql` connector.

## Configuration

Database connector descriptors move to `backend/config/database-connectors.yaml`. The existing
`RECONCILIATION_DATABASE_CONNECTOR_CONFIGURATIONS` JSON setting remains supported for backwards
compatibility, while `RECONCILIATION_DATABASE_CONNECTOR_CONFIG_FILE` selects the YAML file.
Credentials remain environment-owned and are never written to YAML.

An LLM-mapped connector declares only:

- credential reference;
- dialect, database, schema, and table;
- primary key and version column;
- source role;
- `mapping.mode: llm`;
- connector capabilities.

It does not declare `field_columns` or `allowed_columns`.

The new connector uses:

```yaml
seewo-data-mysql:
  credential_reference: secret://connectors/seewo-data-mysql
  dialect: mysql
  database_name: seewo_data
  table_name: data
  primary_key: row_id
  version_column: version
  source_role: target
  mapping:
    mode: llm
```

The credential map gains a separate DSN for `seewo_data`. It may use the same MySQL account and
password as the existing Seewo connector, but the URL database must be `seewo_data`. The
`seewo_writer` account requires `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on
`seewo_data.data`.

## Schema discovery and mapping

Before reading rows, the backend reflects only the configured table and constructs a bounded
schema profile containing column references, names, SQL types, nullability, primary-key status,
and generated/autoincrement status. It does not send row values, credentials, DSNs, or arbitrary
SQL to the model.

For `mapping.mode: llm`, `understand-organization-database-schema@1.0.0` maps the reflected
physical columns to the fixed contract:

- `category`;
- `name`;
- `number`;
- `class_name`;
- `phone`;
- `email`.

The backend validates that:

- every target contract field is mapped exactly once;
- every physical reference belongs to the configured table;
- physical columns are not reused;
- primary-key and version columns are not mapped as ordinary organization fields;
- target mappings remain compatible with server-owned write policy;
- unresolved or ambiguous mappings stop ingestion with a safe data error.

The validated mapping is cached by tenant, connector identities, schema fingerprints, ingestion
contract version, Skill name, and Skill version. A schema change invalidates the cache. Every task
also persists its resolved mapping in an ingestion checkpoint so later phases consume immutable
task facts rather than rerunning the model.

## Bounded data access

Schema reflection may inspect all metadata for the configured table. Row reads begin only after a
mapping is validated and frozen. The effective row-access set is derived at runtime as:

```text
primary key + version column + frozen six-field physical mapping
```

All other columns remain unread and unwritable during ingestion, governance execution,
verification, and rollback. The model never receives a generic SQL tool.

## Execution and rollback

The frozen target mapping becomes an explicit execution input. SQL mutation, verification,
comparison, and rollback code must not read `configuration.field_columns`. They resolve the exact
task mapping checkpoint and reject missing, stale, malformed, or cross-connector mappings.

Existing explicitly mapped connectors remain compatible. Their configured mapping is validated
and frozen through the same checkpoint contract without a model call.

## Generated primary keys

Schema discovery records whether the primary key is generated or auto-incrementing. For a
generated primary key:

- create operations omit the physical primary-key column;
- MySQL generates the key;
- the connector captures the inserted primary key;
- read-after-write verification uses that key;
- the operation stores the resulting database locator;
- rollback uses the persisted locator.

Update and delete continue to require the exact persisted locator and expected-before values.
Explicitly assigned primary keys retain the existing behavior.

## Failure behavior

- Missing YAML files, duplicate connector IDs, invalid YAML, unsupported mapping modes, or
  credential references fail settings validation.
- Connector/schema reflection failures produce sanitized connector errors.
- Model mapping failure follows the existing bounded initial attempt plus at most three retries.
- Unresolved mappings do not read rows or create executable governance operations.
- A connector lacking database or table privileges reports unavailable and does not start
  ingestion.
- Historical tasks continue using their persisted workflow, graph, source binding, and mapping
  checkpoint contracts.

## Testing

Tests cover:

- YAML loading and backwards-compatible environment JSON;
- short LLM-mapped connector validation without physical mappings or allow-lists;
- schema profiles and model mapping on first use;
- cache reuse and schema-fingerprint invalidation;
- fail-closed unresolved, duplicate, missing, and forbidden mappings;
- row reads limited to the frozen physical mapping;
- governance execution and rollback consuming the frozen mapping;
- generated MySQL primary-key create, verification, and rollback behavior;
- coexistence of `seewo-mysql` and `seewo-data-mysql`;
- sanitized handling of missing MySQL privileges.

## Out of scope

- Dynamic business fields beyond the fixed six-field contract;
- model-generated SQL;
- arbitrary joins or multi-table tasks;
- automatic MySQL privilege administration;
- replacing historical connector or graph versions.

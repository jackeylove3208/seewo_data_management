## ADDED Requirements

### Requirement: Upload paired CSV sources
The system SHALL accept one third-party CSV as the authoritative source and one Seewo CSV as the governance target for each reconciliation task.

#### Scenario: Valid paired upload
- **WHEN** a user uploads both CSV files with supported encodings and non-empty headers
- **THEN** the system creates a reconciliation task and records each file's source role, hash, size, and upload time

#### Scenario: Missing source file
- **WHEN** either the authoritative or target CSV is absent
- **THEN** the system rejects task creation with a field-specific validation error

### Requirement: Validate and map source schemas
The system SHALL validate CSV encoding, headers, row shape, required fields, and configured field mappings before reconciliation.

#### Scenario: Recoverable source values
- **WHEN** a row contains a value that can be normalized without losing meaning
- **THEN** the system accepts the row, records a warning, and preserves the original value

#### Scenario: Missing required mapping
- **WHEN** a required canonical field has no source column mapping
- **THEN** the system stops ingestion and reports the missing mapping before matching begins

### Requirement: Create immutable snapshots
The system SHALL persist immutable raw and canonical snapshots with schema and mapping versions.

#### Scenario: Snapshot creation
- **WHEN** both CSV files pass ingestion
- **THEN** the system stores raw rows, canonical entities, source row numbers, file hashes, and conversion versions under a snapshot identifier

### Requirement: Support connector substitution
The system SHALL access source data through a connector contract that allows CSV connectors to be replaced by future API connectors without changing downstream reconciliation services.

#### Scenario: CSV connector selected
- **WHEN** a task specifies CSV as its source type
- **THEN** the workflow loads entities through the CSV connector and emits the same canonical models expected from an API connector

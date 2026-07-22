## ADDED Requirements

### Requirement: Inspect configured CSV, API, and database sources through connectors
The ingestion sub-agent SHALL use configured connector tools to inspect schemas, versions, pagination, and supported capabilities without receiving raw credentials or arbitrary filesystem, URL, query, or DSN access.

#### Scenario: Configured CSV pair is submitted
- **WHEN** a task supplies readable third-party and Seewo CSV files
- **THEN** the sub-agent uses the existing file/CSV foundation to create immutable source-role snapshots

#### Scenario: Connector is an unconfigured placeholder
- **WHEN** an API or database connector lacks required endpoint, query, version, or authentication configuration
- **THEN** the task records a connector-configuration data error and does not claim that source was read

### Requirement: Normalize new tasks to the three-entity six-field contract
The system SHALL normalize new Agent tasks to department, student, and teacher records with category, name, number, class, phone, and email fields and SHALL treat class as applicable only to students.

#### Scenario: Student row is normalized
- **WHEN** an inspected row is identified as a student
- **THEN** its canonical Agent payload preserves category, name, number, class, phone, email, source locator, source role, and raw-row provenance

#### Scenario: Historical task is read
- **WHEN** an existing task contains legacy class or membership entity records
- **THEN** historical APIs continue to decode them without routing them into the new Agent contract

### Requirement: Apply strict authoritative completeness without mutating authority
The system SHALL mark and exclude an authoritative department or teacher missing any of category, name, number, phone, or email and an authoritative student missing any of those fields or class, SHALL retain marked evidence, and SHALL never propose or perform a third-party write.

#### Scenario: One authoritative student lacks class
- **WHEN** otherwise readable authoritative data contains that incomplete row
- **THEN** the row is marked and invisible to identity work, creates a mandatory AI anomaly analysis, is counted in the report, and cannot produce a third-party mutation

#### Scenario: Entire schema cannot be mapped
- **WHEN** the source cannot supply the required six-field contract
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
The system SHALL persist source locator, stable input order, reason code, affected fields, sanitized evidence, inclusion state, and report disposition for every marked row. CSV order SHALL use physical row number, API order SHALL use a stable cursor and record ID, and database order SHALL use a configured stable primary key; a connector that cannot replay the same order SHALL fail as a data error.

#### Scenario: Report is generated
- **WHEN** the task reaches any terminal report state
- **THEN** the report facts include marked authoritative and target counts and reasons without exposing raw student phone

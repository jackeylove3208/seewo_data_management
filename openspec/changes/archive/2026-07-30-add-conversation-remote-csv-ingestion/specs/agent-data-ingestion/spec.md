## ADDED Requirements

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

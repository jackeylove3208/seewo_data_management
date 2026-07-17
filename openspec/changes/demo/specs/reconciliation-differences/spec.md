## ADDED Requirements

### Requirement: Classify reconciliation differences
The system SHALL classify differences as Seewo missing, Seewo redundant, attribute conflict, structure conflict, or duplicate conflict.

#### Scenario: Authoritative entity has no target match
- **WHEN** a third-party entity has no accepted Seewo match
- **THEN** the system creates a Seewo-missing difference with a proposed create action

#### Scenario: Ambiguous entity awaits confirmation
- **WHEN** entity resolution marks a third-party entity as manual review with a plausible Seewo candidate
- **THEN** the system does not create an executable difference until an operator confirms a match or rejects the candidates as unmatched

#### Scenario: Competing mappings are explicit duplicates
- **WHEN** entity resolution marks multiple authoritative entities as competing for the same Seewo entity
- **THEN** the system creates duplicate-conflict differences that require manual resolution

#### Scenario: Target entity is unconsumed
- **WHEN** a Seewo entity is not matched by any authoritative entity
- **THEN** the system creates a Seewo-redundant difference without automatically deleting the entity

#### Scenario: Matched attributes differ
- **WHEN** a matched pair has non-equivalent governed field values
- **THEN** the system creates an attribute or structure conflict with both values

### Requirement: Preserve difference evidence
The system SHALL bind every difference to its source snapshot, target snapshot, match record, compared fields, and comparison rule version.

#### Scenario: Difference inspection
- **WHEN** a user opens a difference
- **THEN** the API returns the raw references, canonical values, match evidence, and exact fields that produced the difference

### Requirement: Query differences at scale
The system SHALL provide paginated filtering by task, entity type, difference type, analysis status, risk, and resolution status.

#### Scenario: Filter unresolved conflicts
- **WHEN** a user requests unresolved teacher attribute conflicts
- **THEN** only matching difference records are returned with stable pagination metadata

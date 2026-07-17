## ADDED Requirements

### Requirement: Resolve entities in dependency order
The system SHALL resolve organization units and classes before using their mappings as evidence for teachers, students, and memberships.

#### Scenario: Teacher department evidence
- **WHEN** a teacher lacks a shared unique identifier but both department parents are already matched
- **THEN** the matcher includes the parent mapping as evidence when ranking teacher candidates

### Requirement: Prefer deterministic matches
The system SHALL check confirmed historical mappings and stable identifiers before invoking fuzzy or AI-based matching.

#### Scenario: Stable identifier match
- **WHEN** a third-party and Seewo teacher have the same valid employee number
- **THEN** the system matches them deterministically and records the identifier as evidence

### Requirement: Retrieve bounded candidate sets
The system SHALL partition entities by tenant, entity type, and applicable organization context before retrieving a configurable top-K candidate set using lexical and vector indexes.

#### Scenario: Large source collection
- **WHEN** a source contains more entities than can be compared pairwise
- **THEN** each unresolved entity is evaluated only against candidates returned from its compatible partition

### Requirement: Enforce mapping cardinality
The system SHALL prevent multiple authoritative entities from being silently matched to the same Seewo entity.

#### Scenario: Competing candidates
- **WHEN** two source entities select the same Seewo entity with similar scores
- **THEN** the system creates a mapping conflict and requires manual resolution

### Requirement: Persist confirmed mappings
The system SHALL persist match method, confidence, evidence, and confirmation provenance for reuse.

#### Scenario: Reusing a manual match
- **WHEN** a later task encounters a previously confirmed entity pair
- **THEN** the system reuses the mapping unless it has been explicitly revoked

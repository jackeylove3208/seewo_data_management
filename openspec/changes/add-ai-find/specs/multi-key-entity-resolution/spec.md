## ADDED Requirements

### Requirement: Match against versioned alternative key groups
The system SHALL define versioned alternative key groups for each entity type, SHALL require every field inside one group to be present and valid, and SHALL accept a deterministic match when any one complete group resolves to exactly one target entity.

#### Scenario: Student matches one allowed combination
- **WHEN** a student has no student number but the configured `name + phone` group is complete and identifies exactly one Seewo student
- **THEN** the system accepts the mapping and records the group version and every compared field as evidence

#### Scenario: No key group is complete
- **WHEN** every allowed key group contains a missing or invalid field
- **THEN** the system does not claim a deterministic match and forwards the entity to bounded candidate retrieval

### Requirement: Conflicting key evidence cannot auto-match
The system MUST NOT automatically accept a mapping when complete key groups identify different target entities or when one key group identifies multiple targets.

#### Scenario: Phone and email identify different students
- **WHEN** one allowed student key group selects target A and another complete key group selects target B
- **THEN** the system records an explicit conflict with both evidence paths and requires AI or operator review

#### Scenario: Shared family contact is not unique
- **WHEN** a complete key group such as `name + phone` returns more than one target
- **THEN** the group is non-unique and cannot produce an accepted mapping

### Requirement: Source identifiers require an explicit trust policy
The system SHALL treat platform `source_id` values as cross-system matching keys only when a versioned field-mapping policy explicitly marks them as shared business identifiers for the entity type and source pair.

#### Scenario: Synthetic files share student IDs
- **WHEN** both CSV files use the same student IDs but the mapping profile does not mark them as shared business identifiers
- **THEN** the system may use ID equality as candidate evidence but does not auto-match solely from that equality

### Requirement: Parent failures degrade candidate retrieval without cascading to zero candidates
The system SHALL first retrieve candidates inside the resolved organization context and SHALL perform a bounded relaxed retrieval when the source parent is unresolved and strict retrieval returns no candidates.

#### Scenario: Class mapping is unresolved
- **WHEN** a student has no resolved class mapping but has valid identity evidence
- **THEN** the system retrieves candidates from the same tenant and entity type using relaxed context and retains the parent mismatch as risk evidence

#### Scenario: Strict block has candidates
- **WHEN** compatible candidates exist inside the resolved parent context
- **THEN** the system does not widen retrieval beyond that context

### Requirement: Recompute descendants after parent recovery
The system SHALL resolve organization units and classes before teachers, students, and memberships, and SHALL recompute descendant context after a parent mapping is recovered.

#### Scenario: AI recovers a class mapping
- **WHEN** second-pass matching accepts a previously unresolved class
- **THEN** the system rebuilds affected student candidate context before finalizing student mappings

### Requirement: Enforce one-to-one mapping globally
The system MUST prevent two authoritative entities from being automatically assigned to the same Seewo entity and MUST resolve accepted candidate edges as a maximum-confidence one-to-one assignment.

#### Scenario: Two students compete for one target
- **WHEN** two source students both have an accepted candidate edge to the same target student
- **THEN** at most one mapping is accepted and the unresolved competing mapping becomes a conflict with assignment evidence

### Requirement: Preserve mapping decision provenance
Every mapping decision SHALL persist the matching phase, rule version, key group or candidate evidence, confidence, model provenance when applicable, and any superseded decision reference.

#### Scenario: Second pass repairs an unmatched decision
- **WHEN** AI-assisted rematching accepts an entity previously marked unmatched
- **THEN** the original decision remains auditable and the new current decision references it without rewriting history

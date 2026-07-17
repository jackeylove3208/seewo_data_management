## ADDED Requirements

### Requirement: Generate reports on demand
The system SHALL generate a governance report only when a user requests one from a completed or partially completed execution record.

#### Scenario: Report generation
- **WHEN** a user requests a report for an execution record
- **THEN** the system creates a versioned report containing snapshot references, difference statistics, causes, chosen plans, operator identity, results, failures, and rollback status

### Requirement: Preserve historical report inputs
The system SHALL generate a report from persisted execution-time facts rather than current mutable target data.

#### Scenario: Target changed after execution
- **WHEN** the target has changed since the recorded execution
- **THEN** the report still describes the original execution and identifies its fixed input versions

### Requirement: Preflight every rollback
The system SHALL evaluate later modifications, entity dependencies, target drift, operation reversibility, and affected scope before allowing rollback.

#### Scenario: Later execution depends on created entity
- **WHEN** a later batch depends on an entity created by the selected execution
- **THEN** direct rollback is blocked and the conflict is shown for manual resolution

### Requirement: Record rollback as compensation
The system SHALL implement rollback as a newly approved compensation batch and SHALL NOT mutate or delete the original execution record.

#### Scenario: Successful rollback
- **WHEN** a rollback plan is approved and verified
- **THEN** the system creates a new execution record linked to the original and updates the original record's rollback status

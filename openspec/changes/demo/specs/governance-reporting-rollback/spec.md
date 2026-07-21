## ADDED Requirements

### Requirement: Generate reports on demand
The system SHALL generate an append-only governance report version only when an authenticated user requests one from a succeeded or partially failed execution record.

#### Scenario: Repeated report generation
- **WHEN** a user requests a report with a new idempotency key for an execution that already has a report
- **THEN** the system creates the next report version while the same idempotency key returns the original report job

### Requirement: Reuse governed analysis model configuration
The system SHALL use the existing enterprise AI analysis provider, model configuration, retry policy, and task tokenization boundary to synthesize report narrative through a report-specific Skill and Pydantic schema.

#### Scenario: Report narrative is unavailable
- **WHEN** the model is unavailable or its structured output fails validation
- **THEN** the system completes the HTML report with deterministic content and records deterministic fallback provenance

### Requirement: Preserve historical report inputs
The system SHALL generate every report version from a persisted execution-time fact bundle rather than current mutable target data.

#### Scenario: Target changed after execution
- **WHEN** the target has changed since the recorded execution
- **THEN** the report still describes the original execution and identifies its fixed input and output target versions

### Requirement: Restore a historical target state
The system SHALL let an authenticated operator select a historical target version and create one new compensation batch that transforms the current target into the selected historical state without moving or deleting existing history.

#### Scenario: Restore and then move to another historical state
- **WHEN** an operator restores V3 to the content of V1 and later selects historical V2
- **THEN** the system appends V4 with V1 content and V5 with V2 content while preserving V1, V2, V3, and both restore executions

### Requirement: Use AI only for restore assistance
The system MAY use the existing analysis model to read intervening execution facts and reports and propose a structured restore plan, but the system SHALL derive and validate the authoritative operation set from immutable target-version and execution facts.

#### Scenario: AI proposes an incomplete restore
- **WHEN** the AI candidate omits, invents, or changes an operation required by the deterministic restore plan
- **THEN** the system rejects the candidate, uses the deterministic plan, and prevents the Agent from executing target mutations

### Requirement: Preflight every historical restore
The system SHALL evaluate current target drift, version ancestry, successful operation facts, reversibility, dependencies, affected scope, and uncertain verification outcomes before confirmation.

#### Scenario: Current target no longer matches the restore preview
- **WHEN** the current target version changes after restore preview
- **THEN** confirmation is rejected as stale and a new preview is required

#### Scenario: Intervening operation has uncertain outcome
- **WHEN** an operation needed for the restore ended in verification-failed or lacks immutable before/after facts
- **THEN** the restore is blocked with an operation-level conflict

### Requirement: Record restores as append-only compensation
The system SHALL execute every approved restore through the ordinary execution and verification path and SHALL link the restore request, source version, selected historical version, covered execution range, compensation plan, compensation batch, and output version.

#### Scenario: Successful historical restore
- **WHEN** an approved restore plan is executed and verified
- **THEN** the system creates a new execution record and target version whose content matches the selected historical version without mutating prior records

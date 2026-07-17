## ADDED Requirements

### Requirement: Execute only approved plans
The system SHALL execute only differences selected by an authenticated operator and included in a validated execution batch.

#### Scenario: Batch confirmation
- **WHEN** an operator submits selected analyzed differences
- **THEN** the system presents and stores the exact create, update, move, disable, and skip operations before execution starts

### Requirement: Perform preflight validation
The system SHALL check target versions, dependencies, operation ordering, reversibility, and conflicts immediately before mutation.

#### Scenario: Target changed after analysis
- **WHEN** the current target value no longer matches the execution plan's expected before-value
- **THEN** the affected operation is blocked and reported as a preflight conflict

### Requirement: Produce versioned CSV targets
The CSV target connector SHALL apply approved operations to a derived target version and SHALL NOT overwrite an uploaded source file.

#### Scenario: Successful CSV execution
- **WHEN** an approved batch completes
- **THEN** the system stores a new Seewo CSV version linked to its input version and makes it available for inspection or download

### Requirement: Handle dependencies and partial failures
The system SHALL execute parent operations before dependent child operations and record each operation independently.

#### Scenario: One operation fails
- **WHEN** an operation fails while unrelated operations succeed
- **THEN** the batch records a partial-failure state, retains successful results, and identifies retryable failed operations

### Requirement: Verify target state
The system SHALL reload the target through its connector and compare actual state with expected state after execution.

#### Scenario: Connector reports success but state differs
- **WHEN** a mutation call succeeds but verification finds an unexpected value
- **THEN** the operation is recorded as verification-failed and is not reported as successful

### Requirement: Maintain append-only execution records
The system SHALL record operator identity from backend authentication context, timestamps, before and after values, operation results, and related task identifiers.

#### Scenario: Audit an execution
- **WHEN** a user opens an execution record
- **THEN** the system returns the immutable operation history and actor identity without accepting a client-supplied replacement operator ID

## ADDED Requirements

### Requirement: Execute only reviewed current proposals
The system SHALL execute only active `pending_execution` governance proposals selected by an authenticated operator and bound to current difference, analysis, and target snapshot versions.

#### Scenario: Batch confirmation
- **WHEN** an operator submits selected AI-authored or operator-authored proposal versions
- **THEN** the system presents and stores the exact create, update, move, disable, and skip operations before execution starts

#### Scenario: Difference remains manual review
- **WHEN** a difference has no executable AI option and no confirmed operator-authored proposal
- **THEN** the system excludes it from execution rather than treating `manual_review` as a mutation operation

### Requirement: Compile proposals deterministically
The system SHALL build execution operations from persisted proposal facts and backend policy without asking a model to choose fields, operations, ordering, or target mutations.

#### Scenario: AI and manual proposals enter one batch
- **WHEN** a batch contains a persisted AI option and a whitelisted operator edit
- **THEN** the plan builder validates both through the same operation, field, version, risk, and dependency policies

### Requirement: Require proposal and batch review
The system SHALL require a proposal-level review followed by an exact batch preview and backend-authenticated confirmation before mutation.

#### Scenario: Same operator confirms the first release
- **WHEN** the operator who selected or authored proposals confirms their batch preview
- **THEN** the system records proposal creator and batch confirmer separately and permits execution under the first-release approval policy

#### Scenario: Batch contains high-risk operations
- **WHEN** a preview contains a high-risk move, disable, or dependent operation
- **THEN** the system requires explicit high-risk acknowledgement and preserves an optional independent-reviewer field for a future four-eyes policy

### Requirement: Keep model explanation optional
The system MAY use the same enterprise model provider with a separate read-only Skill to explain a governance plan, but model availability or output SHALL NOT affect plan validity or execution eligibility.

#### Scenario: Plan explanation model is unavailable
- **WHEN** the enterprise gateway fails after a deterministic preview is available
- **THEN** the preview remains confirmable and displays that the optional explanation is unavailable

### Requirement: Perform preflight validation
The system SHALL check proposal and difference versions, target versions, expected before-values, dependencies, operation ordering, reversibility, and conflicts immediately before mutation.

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
The system SHALL record proposal references and sources, proposal creator, batch confirmer, optional independent reviewer, timestamps, before and after values, operation results, and related task identifiers from backend-owned context.

#### Scenario: Audit an execution
- **WHEN** a user opens an execution record
- **THEN** the system returns the immutable operation history and actor identity without accepting a client-supplied replacement operator ID

## ADDED Requirements

### Requirement: Create and monitor reconciliation tasks
The Web application SHALL allow users to upload paired CSV files, create a task, and observe ingestion, snapshot, matching, difference, and analysis progress.

#### Scenario: Task progresses asynchronously
- **WHEN** backend task stages change
- **THEN** the task detail view updates through SSE or a documented polling fallback without requiring a full page reload

### Requirement: Review differences with context
The Web application SHALL display authoritative and target values side by side with highlighted fields, organization context, match evidence, AI cause, recommendation, risk, and confidence.

#### Scenario: Difference analysis incomplete
- **WHEN** a row's required analysis is pending or failed
- **THEN** its execution checkbox is disabled and the current analysis state is visible

### Requirement: Confirm batch scope
The Web application SHALL let users select current `pending_execution` proposals and present their AI or operator source, exact operation counts, before/after values, excluded items, and high-risk items before creating an execution batch.

#### Scenario: User submits a filtered selection
- **WHEN** a user confirms selected proposal versions across paginated results
- **THEN** the confirmation view shows the stable selection count, included operations, proposal sources, excluded unresolved items, and risk summary

#### Scenario: High-risk proposal is included
- **WHEN** the deterministic preview contains one or more high-risk operations
- **THEN** the UI requires a separate high-risk acknowledgement before enabling final batch confirmation

#### Scenario: Optional plan explanation fails
- **WHEN** the model-backed plan explanation is unavailable
- **THEN** the UI retains the deterministic counts and before/after preview and allows confirmation under the same backend policy

### Requirement: Monitor execution outcomes
The Web application SHALL show batch progress, successful operations, failed operations, verification failures, and retry eligibility.

#### Scenario: Batch partially fails
- **WHEN** an execution reaches partial-failure state
- **THEN** the UI separates completed and failed operations and offers retry only for eligible failures

### Requirement: Navigate execution history
The Web application SHALL provide execution history and detail views with operator, timestamps, immutable before/after values, report actions, and rollback actions.

#### Scenario: Open historical execution
- **WHEN** a user selects an execution record
- **THEN** the detail view displays its audit timeline and enables report or rollback actions only when backend state permits them

### Requirement: Present rollback impact
The Web application SHALL require users to review rollback preflight results before confirming a compensation batch.

#### Scenario: Rollback conflict exists
- **WHEN** preflight finds later dependencies or target drift
- **THEN** the UI explains the conflicting entities and disables direct rollback

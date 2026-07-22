## ADDED Requirements

### Requirement: Apply server-owned risk policy after all analysis completes
The system SHALL classify risk with versioned backend policy, SHALL keep low-risk operations pending until every analysis/conflict outcome is terminal, and SHALL prevent a model-generated label from lowering risk.

#### Scenario: Missing ordinary field is safely resolved
- **WHEN** one authoritative identity is uniquely established and an absent non-sensitive field can be filled reversibly
- **THEN** policy may classify the operation low risk for automatic execution after analysis completion

#### Scenario: Student phone is governed
- **WHEN** an operation reads for governance or changes a student's phone
- **THEN** policy classifies it high risk regardless of model confidence

### Requirement: Group compatible high-risk work into frozen approval cards
The system SHALL group high-risk findings by server kind, entity type, operation, risk-policy version, and compatible preconditions, SHALL freeze exact member versions and content hash, and SHALL accept one authenticated agree or reject decision for that group.

#### Scenario: Fifty students share one high-risk type
- **WHEN** 50 compatible student findings require the same high-risk operation
- **THEN** the conversation displays one pageable approval card whose decision covers only the frozen 50 records

#### Scenario: Finding changes before approval
- **WHEN** any member version or target value changes after the card is built
- **THEN** the old approval is stale and cannot authorize execution

### Requirement: Interpret conflicts through scoped conversation and second confirmation
The system SHALL temporarily enable conversation input only for the current conflict batch, SHALL convert user text to a structured decision limited to listed candidates/outcomes, SHALL display its interpretation, and SHALL require confirmation before governance planning.

#### Scenario: User chooses number evidence
- **WHEN** the user states that one conflict shall use its listed number-matched student
- **THEN** the system drafts that exact candidate decision and waits for confirm or restate

#### Scenario: User discusses another task
- **WHEN** conflict-mode input attempts to start unrelated synchronization
- **THEN** the backend rejects it without creating another task or releasing the school lock

### Requirement: Execute only persisted validated plans through capable target connectors
The governance sub-agent SHALL execute only server-compiled operations backed by current authoritative evidence, target version, risk decision, connector capability, and idempotency key and SHALL never modify the authoritative connector.

#### Scenario: CSV operation executes
- **WHEN** an approved plan targets CSV
- **THEN** execution creates and verifies a new target version without overwriting the original file

#### Scenario: Connector lacks an operation
- **WHEN** the target API or database adapter does not support a proposed operation
- **THEN** the operation remains visible but non-executable and is reported without a false success

### Requirement: Continue independent work after partial execution failure
The system SHALL retry retryable connector operations at most three times, SHALL block dependent operations after failure, SHALL continue independent operations, and SHALL not automatically roll back verified successes.

#### Scenario: One operation fails in a batch
- **WHEN** operation 21 fails after 20 verified successes
- **THEN** dependencies of operation 21 are blocked, unrelated operations continue, and the final report preserves all individual outcomes

#### Scenario: User terminates during execution
- **WHEN** termination is requested after some operations succeeded
- **THEN** no new operations start, the current atomic connector unit drains or aborts safely, prior successes remain, and reporting begins

### Requirement: Consume frozen analysis contracts without changing reconciliation
The governance milestone SHALL consume only persisted, versioned findings, selected solutions, risk decisions, approvals, and target versions from the analysis milestone. It SHALL NOT rerun identity matching, modify authoritative data, alter a finding's evidence membership, or use legacy difference rows as a substitute for a new-Agent finding.

#### Scenario: A finding changes after plan compilation
- **WHEN** the finding, solution, target version, or approval membership no longer matches the frozen plan input
- **THEN** the backend rejects execution and requires a new versioned analysis or plan rather than mutating with stale evidence

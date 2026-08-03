# Agent reconciliation analysis Specification

## Purpose

Define deterministic identity work, bounded AI analysis, and the immutable handoff to governance.

## Requirements

### Requirement: Build task-scoped ordinary identity indexes
The system SHALL index normalized authoritative number, phone, and email values in PostgreSQL by tenant, snapshot, and entity type and SHALL NOT create or query embedding vectors for new Agent tasks.

#### Scenario: Identity work begins
- **WHEN** valid authoritative snapshots are ready
- **THEN** the backend creates/reuses idempotent student, teacher, and department identity postings for the exact task snapshots

#### Scenario: Same value occurs more than once
- **WHEN** one candidate key points to multiple authoritative records
- **THEN** the evidence records every hit and routes the target record to conflict work rather than selecting one silently

### Requirement: Construct complete target-to-authority work items
For every Seewo row, the system SHALL query authoritative student, teacher, and department candidates in that order using all supplied identity keys and SHALL persist resolved, conflict, target-extra, or target-missing work with immutable evidence.

#### Scenario: Missing number is recoverable
- **WHEN** a Seewo row has no number but its phone or email uniquely identifies one authoritative record
- **THEN** the system establishes a candidate correspondence and treats missing number and other fields as ordinary differences

#### Scenario: Identity keys contradict each other
- **WHEN** number, phone, or email identify different authoritative records
- **THEN** the system creates an identity-conflict work item and does not allow search order to choose a winner

#### Scenario: Nothing matches
- **WHEN** none of the supplied identity keys matches any authoritative category
- **THEN** the system creates a target-extra work item with an AI-analysis requirement even when name, category, or class resembles an unclaimed authority row

#### Scenario: Multiple target rows resolve to one authority row
- **WHEN** more than one target row resolves to the same authoritative record
- **THEN** the earliest connector-stable target row retains the correspondence and every later row becomes target-extra/duplicate work without allowing model output or worker timing to change that order

#### Scenario: Authority remains unclaimed
- **WHEN** a valid authoritative record has no resolved Seewo correspondence after all target rows are processed
- **THEN** the system creates a target-missing work item eligible for a create-oriented solution

### Requirement: Analyze immutable batches containing at most ten work items
The analysis sub-agent SHALL receive no more than 10 distinct persisted work items per DeepSeek call and SHALL validate exact response membership, uniqueness, candidate references, fields, and output schema before committing outcomes. This AI batch limit SHALL NOT limit the complete authoritative PostgreSQL index searched for each target row.

#### Scenario: Forty-three items need analysis
- **WHEN** one entity group contains 43 terminally constructed work items
- **THEN** the backend creates five independently retryable batches containing 10, 10, 10, 10, and 3 items

#### Scenario: Response omits an item
- **WHEN** model output lacks one requested work-item ID or duplicates another
- **THEN** the batch attempt fails validation and no fabricated result is committed for the omitted item

### Requirement: Generate actionable AI findings and keep correct records silent
The analysis sub-agent SHALL produce a server-owned finding kind, Chinese category, evidence-backed explanation, risk, and one to three governance solutions for every actionable outcome and SHALL create no finding, proposal, or workbench row for a correct record.

#### Scenario: Resolved record has missing class
- **WHEN** identity is confirmed and the authoritative student class is absent in Seewo
- **THEN** an ordinary field-missing finding and authoritative completion solution are produced

#### Scenario: Record is fully correct
- **WHEN** identity and every governed field agree
- **THEN** only correspondence/progress evidence is stored and no user-facing finding is created

### Requirement: Collect unresolved identity evidence for human clarification
The system SHALL aggregate identity conflicts into persisted clarification batches, SHALL provide only masked normalized candidates and allowed outcomes, and SHALL not create executable governance work until the user-confirmed structured decision exists.

#### Scenario: User explanation is ambiguous
- **WHEN** the clarification Skill cannot bind the user's text to exactly one listed candidate or outcome
- **THEN** it requests a restatement and leaves the conflict unresolved

### Requirement: Publish an immutable analysis-to-governance handoff
The analysis milestone SHALL publish only validated actionable findings to later milestones. Each published finding SHALL retain immutable work-item membership and evidence references, a Chinese category and explanation, server-owned risk, one to three validated target-only solutions, and exactly one recommended solution; correct rows SHALL remain absent from the user-facing handoff.

#### Scenario: A governance implementation consumes an analysis finding
- **WHEN** a later governance milestone reads an analysis finding
- **THEN** it uses the persisted versioned finding and solution IDs and does not recompute identity matching, alter batch membership, or accept model prose as an executable operation

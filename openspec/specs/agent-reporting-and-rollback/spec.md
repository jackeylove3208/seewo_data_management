# Agent reporting and rollback Specification

## Purpose

Define fact-grounded terminal reporting, protected history, and independent rollback tasks.

## Requirements

### Requirement: Generate a fact-grounded terminal report for every terminal task
The system SHALL persist normal, partial-success, data-error, model-error termination, user-termination, and rollback reports using immutable ingestion, Agent, approval, execution, and version facts.

#### Scenario: Data contract cannot be mapped
- **WHEN** a task skips reconciliation because source structure is incompatible
- **THEN** a data-error report explains affected source/fields and states that no governance mutation occurred

#### Scenario: Execution is partial
- **WHEN** some operations succeed and others fail or are blocked
- **THEN** the report presents exact success, failure, blocked, rejected, and skipped counts from execution facts

### Requirement: Use reports as navigation but not rollback truth
The system SHALL expose rollback eligibility and entry points through reports but SHALL derive restore operations only from verified successful execution attempts, before/after evidence, dependencies, and target versions.

#### Scenario: Narrative contains a suggested rollback
- **WHEN** no verified execution fact supports that suggestion
- **THEN** the rollback planner ignores it and creates no restore operation

### Requirement: Create every rollback as a new exclusive high-risk task
The system SHALL create a new task/run/history record for each rollback request, acquire the school lock, generate a deterministic restore preview, require authenticated approval, execute compensating operations, verify them, and generate a rollback report.

#### Scenario: Another sync is active
- **WHEN** a user requests rollback while the school lock is owned
- **THEN** the rollback task does not start or mutate data

#### Scenario: Current target drift conflicts with restore
- **WHEN** target values changed after the original execution
- **THEN** rollback is blocked or routed through bounded conflict clarification rather than overwriting newer data

### Requirement: Protect mutation-bearing history from deletion
The backend SHALL compute deletion eligibility from verified successful target mutations and SHALL reject deletion of any sync or rollback task with at least one such mutation.

#### Scenario: All proposals were rejected
- **WHEN** a report exists but no target operation succeeded
- **THEN** the task is deletion-eligible

#### Scenario: One operation succeeded
- **WHEN** a partial task contains one verified target mutation
- **THEN** deletion is blocked and its report, execution evidence, versions, and restore chain remain retained

### Requirement: Provide backend-owned paged history
The system SHALL return tenant-safe paged history for all report-bearing tasks with title, kind, terminal state, issue/operation summaries, report link, rollback eligibility, and deletion eligibility.

#### Scenario: User opens history on another browser
- **WHEN** the same authenticated school loads the workspace without prior local storage
- **THEN** it sees the persisted task and rollback history from the backend

### Requirement: Derive downstream records only from immutable prior facts
The reporting and rollback milestone SHALL consume persisted ingestion marks, analysis findings, approvals, verified execution attempts, target versions, and task events through versioned read contracts. It SHALL NOT convert model narrative into execution or restore facts, rewrite completed execution evidence, or expose an abnormal-input report as rollback evidence.

#### Scenario: A report narrative recommends an unsupported restore
- **WHEN** the narrative suggests a restore action absent from verified execution facts
- **THEN** the report may display the narrative as non-authoritative context but the rollback planner excludes it

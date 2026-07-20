# Reconciliation workflow orchestration Specification

## Purpose

Define tenant-safe, bounded, persistent, idempotent workflow advancement and retry behavior for reconciliation tasks.

## Requirements

### Requirement: Backend owns task tenant identity
The system SHALL derive a reconciliation task's tenant from authenticated backend operator context and SHALL NOT trust a client-supplied tenant identifier.

#### Scenario: Frontend creates a task
- **WHEN** an authenticated operator submits paired uploads and task settings without a tenant identifier
- **THEN** the backend creates the task and both snapshots in the operator's tenant

#### Scenario: Operator accesses another tenant's task
- **WHEN** an operator requests, advances, or retries a task outside the operator's tenant
- **THEN** the backend returns not found without revealing whether that task exists

### Requirement: Automatically progress the reconciliation workflow
The system SHALL advance a created task through snapshot readiness, entity resolution, difference detection, and mandatory analysis in that order without requiring the user to invoke each domain API manually.

#### Scenario: Ingestion completes successfully
- **WHEN** the task detail client observes a task with published source and target snapshots
- **THEN** it automatically requests workflow advancement until the task reaches analysis-ready or a terminal failure

#### Scenario: Page is refreshed during matching
- **WHEN** the user reloads or later reopens a task whose workflow is incomplete
- **THEN** the client reads persisted backend state and resumes from the first incomplete stage

### Requirement: Bound each workflow advancement
The backend SHALL process at most one deterministic stage or one configured AI analysis batch in a single advancement request.

#### Scenario: A task has many differences
- **WHEN** one advancement request reaches mandatory analysis with more items than the configured batch size
- **THEN** the backend analyzes only the bounded batch, persists progress, and reports that additional advancement is required

### Requirement: Persist observable workflow progress
The system SHALL persist current stage, status, attempt count, completed work, total work, timestamps, and structured errors independently of browser state.

#### Scenario: AI batch completes
- **WHEN** a bounded analysis batch finishes
- **THEN** the task response reports total, completed, succeeded, manual-only, and failed analysis counts from persisted records

#### Scenario: Backend process restarts
- **WHEN** the API process restarts after a completed stage
- **THEN** the next advancement reuses persisted outputs and does not regenerate completed snapshots, matches, or differences

### Requirement: Prevent concurrent duplicate advancement
The workflow service SHALL serialize advancement for one task and SHALL make stage operations idempotent.

#### Scenario: Two clients advance the same task
- **WHEN** two advancement requests arrive concurrently for the same task and stage
- **THEN** at most one request creates new stage outputs and both clients subsequently observe the same persisted state

### Requirement: Expose safe retry behavior
The system SHALL classify workflow failures with a stable code and retryable flag and SHALL only offer retry for retryable failures.

#### Scenario: Enterprise gateway times out
- **WHEN** model retries are exhausted for a bounded batch
- **THEN** affected differences receive a policy-compliant manual state or retryable failure, and completed differences remain complete

#### Scenario: Snapshot contract is invalid
- **WHEN** advancement fails because required immutable snapshots are absent or incompatible
- **THEN** the task exposes a non-retryable error and the UI does not offer blind retry

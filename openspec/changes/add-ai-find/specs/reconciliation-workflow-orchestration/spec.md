## MODIFIED Requirements

### Requirement: Automatically progress the reconciliation workflow
The system SHALL advance a created task through snapshot readiness, initial entity resolution, AI-assisted rematching when unresolved entities exist, matching quality evaluation, difference detection, and mandatory governance analysis in that order without requiring the user to invoke each domain API manually.

#### Scenario: Ingestion completes successfully
- **WHEN** the task detail client observes a task with published source and target snapshots
- **THEN** it automatically requests workflow advancement until the task reaches a running durable job, analysis-ready state, quality-gate failure, or another terminal failure

#### Scenario: Page is refreshed during rematching
- **WHEN** the user reloads or later reopens a task whose rematching job is incomplete
- **THEN** the client reads persisted backend state, observes the existing job, and does not enqueue duplicate rematching work

#### Scenario: Matching has no unresolved entities
- **WHEN** initial entity resolution accepts all entities and the quality policy passes
- **THEN** the workflow skips model-assisted rematching and advances directly to formal difference detection

### Requirement: Persist observable workflow progress
The system SHALL persist current stage, status, attempt count, completed work, total work, timestamps, structured errors, rematching job ID, rematching counters, and matching quality result independently of browser state.

#### Scenario: One rematching item completes
- **WHEN** a worker commits an accepted, no-match, manual-review, conflict, or failed rematching outcome
- **THEN** the task response exposes the updated real counters without waiting for a fixed-size batch

#### Scenario: Quality gate blocks progress
- **WHEN** the matching quality policy fails
- **THEN** the persisted workflow identifies the affected entity type, stable failure code, observed metric, threshold, and retryability

#### Scenario: Backend process restarts
- **WHEN** the API or worker restarts after a completed deterministic stage or rematching item
- **THEN** the next advancement reuses persisted snapshots, mappings, candidate edges, job outcomes, and quality results without regenerating committed work

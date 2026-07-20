## ADDED Requirements

### Requirement: Analysis runs as a durable job
The system SHALL create or reuse a durable `analysis-v3` job for a reconciliation task and SHALL return without waiting for model calls to finish.

#### Scenario: Workflow reaches AI analysis
- **WHEN** an authorized operator advances a task whose difference detection is complete
- **THEN** the system creates one work item for every current difference version, returns the job identifier, and does not execute the model inside the workflow request

#### Scenario: Duplicate creation request
- **WHEN** the same tenant submits the same analysis job idempotency key again
- **THEN** the system returns the original job and does not create duplicate work items

### Requirement: Worker claims one item with a recoverable lease
The system MUST claim work items with a committed lease before external model execution and MUST NOT hold a database transaction or task row lock while waiting for the model gateway.

#### Scenario: Two workers compete
- **WHEN** two workers request available work at the same time
- **THEN** each difference work item is leased to at most one worker by using row locking with skip-locked semantics

#### Scenario: Worker terminates after claiming
- **WHEN** a worker does not complete an item before its lease expires
- **THEN** another worker can reclaim the item without duplicating an immutable analysis result

### Requirement: Each item commits independent progress
The system SHALL persist the terminal outcome of each difference in its own short transaction and SHALL update job counters atomically with that outcome.

#### Scenario: One item completes
- **WHEN** a worker persists a successful, manual-required, or failed item
- **THEN** the job completed count increases by one and is visible to other requests immediately

#### Scenario: Later item fails
- **WHEN** a later item cannot be processed
- **THEN** previously committed analysis results remain available and are not rolled back

### Requirement: Retry policy distinguishes technical and governance outcomes
The system SHALL retry only transient technical failures with bounded exponential backoff. Information gaps, high risk, and policy-required manual handling SHALL be successful manual-required outcomes rather than technical failures.

#### Scenario: Gateway timeout
- **WHEN** the model gateway times out and attempts remain
- **THEN** the work item enters retry wait with a future availability time and retains its attempt history

#### Scenario: High-risk difference
- **WHEN** policy determines that a difference is high risk
- **THEN** the work item completes as manual-required with actionable Chinese manual steps and is not retried automatically

#### Scenario: Retries exhausted
- **WHEN** transient attempts are exhausted but the difference can still be read
- **THEN** the system persists a Chinese safe manual fallback with the technical failure code separated from business-visible text

### Requirement: Progress survives refresh and connection loss
The system SHALL expose persisted job status through a status endpoint and an SSE event stream with a polling-compatible representation.

#### Scenario: SSE progress update
- **WHEN** a work item commits a terminal outcome
- **THEN** connected clients receive a new job snapshot event with a monotonic cursor and updated real counters

#### Scenario: SSE unavailable
- **WHEN** the event connection fails or an intermediary does not support streaming
- **THEN** the client polls the job status endpoint and displays the same persisted counters

#### Scenario: Browser reopens task
- **WHEN** an operator refreshes or returns while a job is queued or running
- **THEN** the page resumes from the persisted job identifier and does not enqueue a duplicate job

### Requirement: Job control is tenant-scoped and auditable
The system MUST enforce tenant ownership for job read, retry, cancel, and event APIs and MUST preserve requester, timestamps, attempts, leases, and stable error codes for audit.

#### Scenario: Cross-tenant access
- **WHEN** an operator requests a job belonging to another tenant
- **THEN** the system returns not found without exposing job metadata

#### Scenario: Retry failed subset
- **WHEN** an authorized operator retries a terminal job containing retryable failed items
- **THEN** only eligible failed items are requeued and completed or manual-required items remain unchanged


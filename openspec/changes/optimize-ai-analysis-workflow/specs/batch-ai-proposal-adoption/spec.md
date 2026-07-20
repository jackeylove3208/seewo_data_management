## ADDED Requirements

### Requirement: Issue summaries appear only after terminal analysis
The workbench SHALL hide the problem-type summary until every current analysis work item has entered a terminal state and SHALL then use backend aggregation across the full task.

#### Scenario: Analysis is running
- **WHEN** an analysis job is queued or running
- **THEN** the task page shows analysis progress and does not render the problem-type summary heading, table, or placeholder rows

#### Scenario: One entity type has no issues
- **WHEN** terminal aggregation reports zero issues for an entity type
- **THEN** the task page omits that entity type

#### Scenario: Task has no issues
- **WHEN** terminal aggregation reports zero issues for all entity types
- **THEN** the task page displays a Chinese completed empty state and does not show the batch adoption action

### Requirement: Backend provides complete issue aggregation
The system SHALL aggregate all current difference versions by entity type and return issue count, proposal-ready count, needs-information count, manual-only count, and failed count without depending on paginated list limits.

#### Scenario: Task contains more than one page of differences
- **WHEN** a task contains more differences than the list API page size
- **THEN** the summary counts include every current difference in the task

### Requirement: Batch preview selects only safe recommended paths
The system SHALL provide task-level and optional entity-type-level previews that select recommended low/medium-risk `auto_executable` paths and classify every excluded item by a stable reason.

#### Scenario: Preview contains mixed outcomes
- **WHEN** a task has executable, high-risk, needs-information, manual-only, failed, stale, and already-proposed items
- **THEN** the preview lists executable items for adoption and groups every other item under its exclusion reason with Chinese labels

#### Scenario: Analysis job is not terminal
- **WHEN** an operator requests batch preview before the referenced job is terminal
- **THEN** the system rejects the preview and returns the current analysis progress

### Requirement: Batch confirmation creates pending-execution proposals only
The system SHALL confirm a batch by copying server-owned analysis actions into immutable `pending_execution` governance proposals after revalidating tenant, analysis version, difference version, field policy, and before values.

#### Scenario: Operator confirms preview
- **WHEN** an authorized operator confirms a valid preview token
- **THEN** the system creates pending-execution proposals for still-valid included items and does not invoke a target connector or modify source snapshots

#### Scenario: Client alters analysis content
- **WHEN** a confirmation request includes or attempts to substitute operation or field-change content
- **THEN** the system ignores or rejects client-authored action data and uses only persisted analysis content

### Requirement: Batch confirmation is idempotent and supports partial success
The system MUST return the same result for a repeated confirmation idempotency key and MUST preserve successful proposals when another item fails validation.

#### Scenario: One difference becomes stale after preview
- **WHEN** one included difference changes before confirmation
- **THEN** the system skips that item as a version conflict, creates proposals for other valid items, and returns separate success, skipped, and failed counts

#### Scenario: Confirmation is retried
- **WHEN** the same tenant repeats a confirmation with the same idempotency key
- **THEN** the system returns the original batch result without creating duplicate proposal versions

### Requirement: Manual and individual paths remain available
The workbench SHALL preserve individual AI adoption and schema-driven manual editing for items excluded from batch processing, and both paths SHALL continue to create pending-execution proposals.

#### Scenario: User opens manual-only item
- **WHEN** an item has only a manual resolution path
- **THEN** the UI shows the Chinese manual steps and an entry to the existing manual editor without a misleading AI adoption button

#### Scenario: User chooses manual edit for an executable item
- **WHEN** an operator prefers manual changes over the recommended AI path
- **THEN** the UI opens the manual editor and does not write directly to the source system

### Requirement: Batch workbench reports localized results
The task page SHALL offer `AI 一键处理` only when at least one proposal-ready item exists and SHALL show a Chinese preview, confirmation state, and partial-result summary.

#### Scenario: Batch completes with exclusions
- **WHEN** confirmation creates some proposals and skips other items
- **THEN** the UI reports created, skipped, and failed totals in Chinese and provides navigation to pending governance execution and remaining manual items


# Reconciliation left workspace Specification

## Purpose

Define the persistent navigation workspace and backend-owned task history.

## Requirements

### Requirement: Start a new reconciliation from the workspace
The left workspace SHALL retain “新建对话”; “外部数据同步” SHALL be available only from the task-home primary action. Both entries SHALL respect backend school-lock state and route eventual task creation through the same supervisor runtime.

#### Scenario: User opens a new conversation while idle
- **WHEN** no school task is active
- **THEN** a persistent conversation opens without changing history

#### Scenario: User opens another start entry while locked
- **WHEN** a sync or rollback owns the school lock
- **THEN** the workspace exposes the active task and does not permit a competing start

### Requirement: Navigate recent task history
The left workspace SHALL load concise recent sync and rollback task summaries from paged backend history and SHALL allow each report-bearing item to open persisted task/report details.

#### Scenario: User uses a new browser
- **WHEN** authenticated school history exists but local storage is empty
- **THEN** recent history is still displayed from the backend

#### Scenario: More history exists
- **WHEN** the recent limit is exceeded
- **THEN** the complete history entry opens backend-paged history without discarding current state

### Requirement: Show direct operational summaries
Each history item SHALL display title, task/report kind, terminal status, creation/completion time, and issue/operation summary and SHALL omit hashes, snapshot IDs, connector secrets, raw student phone, and internal model errors.

#### Scenario: Task ended without mutation
- **WHEN** a data-error or terminated no-write task is shown
- **THEN** its distinct state and deletion eligibility are visible

#### Scenario: Task partially changed data
- **WHEN** at least one target mutation succeeded
- **THEN** the item shows partial/complete execution state, report/rollback access, and no delete control

### Requirement: Enforce backend-owned history deletion eligibility
The workspace SHALL show task deletion only when the backend reports no verified successful target mutation and SHALL retain an item after any failed or blocked delete response.

#### Scenario: All work was rejected or failed before mutation
- **WHEN** `deletion_eligible` is true
- **THEN** the user can request deletion and the item disappears only after backend success

#### Scenario: One governance or rollback write succeeded
- **WHEN** `deletion_eligible` is false
- **THEN** the workspace omits deletion and preserves the report/restore history

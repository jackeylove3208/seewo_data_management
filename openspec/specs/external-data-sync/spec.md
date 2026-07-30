# External data sync Specification

## Purpose

Define the manual Agent data-sync entry that starts the shared supervisor runtime.

## Requirements

### Requirement: Enter a manual external data sync
The Web application SHALL provide an “外部数据同步” page with one manual-sync entry and SHALL reveal configured CSV/API/database source controls only after that entry is activated; it SHALL NOT provide an automatic-sync option.

#### Scenario: User opens external data sync
- **WHEN** the user navigates to the page
- **THEN** it shows the manual entry without an automatic-sync command

#### Scenario: User starts manual sync
- **WHEN** the user activates manual sync
- **THEN** it reveals available configured source/target controls and entity selection in one focused form

### Requirement: Initialize independent task information
The manual-sync form SHALL initialize a task name and department/student/teacher selection, SHALL fix synchronization scope to the whole school and full processing, and SHALL omit reconciliation-scope and processing-mode controls.

#### Scenario: User enters sync directly
- **WHEN** the page is opened without a conversation
- **THEN** it requires no conversation handoff and displays no scope or full/partial selector

### Requirement: Collect paired CSV files for manual sync
When CSV is selected, the form SHALL require one third-party authoritative CSV and one Seewo target CSV and SHALL show each readable file name, validation state, and row summary without internal hashes.

#### Scenario: Both CSV files are valid
- **WHEN** readable paired files are selected
- **THEN** their summaries remain available for Agent task submission

#### Scenario: One file is invalid
- **WHEN** one selected CSV cannot be inspected
- **THEN** the form identifies it, preserves the other input and entity choices, and permits replacement

### Requirement: Create a reconciliation task from manual sync
The page SHALL enable “开始同步” only when task name, at least one of department/student/teacher, and the selected connector inputs are valid, and SHALL submit one idempotent start command to the same supervisor runtime as conversational sync.

#### Scenario: User starts synchronization
- **WHEN** the valid form is submitted and no school task owns the lock
- **THEN** one Agent task is created, the school lock is acquired, and the UI navigates to persisted progress

#### Scenario: School is already locked
- **WHEN** another sync or rollback is active
- **THEN** submission is rejected without duplicate uploads, tasks, or mutations

### Requirement: Preserve the existing downstream workflow
Tasks created through manual external data sync SHALL use the new supervisor/sub-agent lifecycle, Skills, MCP permissions, analysis, approvals, execution, and reports rather than legacy matching, difference-gate, rematching, or browser-owned progression.

#### Scenario: Task creation succeeds
- **WHEN** manual input is accepted
- **THEN** its task detail exposes the same Agent phases and commands as a conversationally created task

### Requirement: Recover from manual-sync failures
The page SHALL preserve valid user-selected inputs after pre-task failures and SHALL render post-start failures from persisted Agent state without allowing a second task while the first owns the school lock.

#### Scenario: Backend rejects task creation
- **WHEN** validation or lock acquisition fails before task creation
- **THEN** the completed form remains available with a readable retryable error

### Requirement: Keep remote links out of manual synchronization
The manual-sync page and manual task API SHALL remain limited to their existing configured or
uploaded source contracts and SHALL NOT expose or accept `remote_csv`, a remote URL, or a
`remote_source_id`.

#### Scenario: User opens manual sync
- **WHEN** the manual-sync page is rendered
- **THEN** it shows no remote-link input or remote-source option

#### Scenario: Client forges a manual remote source
- **WHEN** a client calls the manual task endpoint with `remote_csv`, a URL, or a `remote_source_id`
- **THEN** validation rejects the request before a task, run, source binding, or school lock is created

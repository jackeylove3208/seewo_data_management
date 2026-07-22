## ADDED Requirements

### Requirement: Enter a manual external data sync
The Web application SHALL provide an “外部数据同步” page whose initial state exposes one available “手动同步” command and SHALL NOT render an automatic-sync option or CSV controls before that command is activated.

#### Scenario: User opens external data sync
- **WHEN** the user navigates to “外部数据同步”
- **THEN** the page shows “手动同步” without showing an automatic-sync entry or either CSV selector

#### Scenario: User starts manual sync
- **WHEN** the user activates “手动同步”
- **THEN** the page reveals the paired CSV selectors and editable task information in one continuous form

### Requirement: Initialize independent task information
The manual-sync form SHALL initialize the existing default task title, scope, entity types, and processing mode without requiring or receiving a current conversation handoff.

#### Scenario: User enters sync directly
- **WHEN** the user opens external data sync from the left workspace and activates “手动同步”
- **THEN** the manual-sync form uses the existing default task information and does not require a prior conversation

### Requirement: Collect paired CSV files for manual sync
The manual-sync form SHALL require one third-party source CSV and one Seewo target CSV and SHALL show each readable file name, validation state, and row summary without exposing internal hashes.

#### Scenario: Both CSV files are valid
- **WHEN** the user selects readable CSV files for both source roles
- **THEN** the form shows the two direct data summaries and retains the files for task creation

#### Scenario: One CSV file is invalid
- **WHEN** one selected CSV cannot be read or summarized
- **THEN** the form identifies the affected file, preserves the valid file and all task information, and allows replacement

### Requirement: Create a reconciliation task from manual sync
The page SHALL enable “开始同步” only when both CSV files and all required task fields are valid, and SHALL submit through the existing idempotent upload and reconciliation-task creation service.

#### Scenario: Manual-sync form becomes valid
- **WHEN** both CSV files and the title, scope, entity types, and processing mode are valid
- **THEN** “开始同步” becomes available and clearly represents the primary next action

#### Scenario: User starts synchronization
- **WHEN** the user activates “开始同步” on a valid form
- **THEN** the application uploads both files, creates one reconciliation task, refreshes recent history, and navigates to that task's detail page

#### Scenario: Submission is already pending
- **WHEN** upload or task creation is in progress
- **THEN** the page disables repeated submission while retaining the entered information

### Requirement: Preserve the existing downstream workflow
Tasks created through manual external data sync SHALL continue through data ingestion, entity resolution, difference detection, and AI analysis using the existing backend contracts and task-detail presentation.

#### Scenario: Task creation succeeds
- **WHEN** the manual-sync service returns a created task
- **THEN** the task detail presents the existing processing stages without introducing an external-sync backend mode

### Requirement: Recover from manual-sync failures
The external-data-sync page SHALL preserve valid files and task information after upload or task-creation failures and SHALL allow an idempotent retry with a user-facing error.

#### Scenario: Backend task creation fails
- **WHEN** the existing task creation service rejects or fails a request
- **THEN** the page remains on the completed form, displays a readable error, and allows retry without intentionally creating a duplicate task

### Requirement: Present a focused operational interface
The external-data-sync page SHALL use a restrained enterprise-workbench hierarchy, an unframed constrained form, direct labels, stable control dimensions, and responsive layouts that do not overflow or obscure actions on supported desktop and mobile widths.

#### Scenario: User views manual sync on desktop
- **WHEN** the manual-sync form is shown on a desktop viewport
- **THEN** sync context, source files, task settings, validation feedback, and the primary action are visually ordered for repeated operational use without a chat column or a “任务草案” side panel

#### Scenario: User views manual sync on mobile
- **WHEN** the manual-sync form is shown on a narrow viewport
- **THEN** method entry, file selectors, fields, choices, errors, and actions stack without horizontal overflow or overlapping controls

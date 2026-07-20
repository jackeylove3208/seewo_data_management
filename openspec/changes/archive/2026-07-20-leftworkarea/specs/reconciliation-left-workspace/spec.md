## ADDED Requirements

### Requirement: Provide a persistent left workspace
The Web application SHALL render product identity, primary reconciliation navigation, recent task history, and connection status in a shared left workspace for every reconciliation route.

#### Scenario: User navigates between workbench pages
- **WHEN** the user opens the task list, task creation, task detail, or difference detail route
- **THEN** the same left workspace remains available while the selected page is rendered in the main content area

### Requirement: Start a new reconciliation from the workspace
The left workspace SHALL provide a visually prominent “新建对账” command that opens a fresh conversational task-creation session.

#### Scenario: User starts another reconciliation
- **WHEN** the user selects “新建对账” while viewing a historical task
- **THEN** the application navigates to a new empty creation session without mutating the historical task

### Requirement: Navigate recent task history
The left workspace SHALL show concise recent-task summaries and SHALL allow each available history item to open its task detail.

#### Scenario: User opens a historical task
- **WHEN** the user selects a history item in the left workspace
- **THEN** the application navigates to that task, highlights it as current, and preserves its persisted task and selection state

#### Scenario: User needs the full history
- **WHEN** more tasks exist than the left workspace displays
- **THEN** the workspace provides an entry to the complete task list without discarding the current task

### Requirement: Show direct operational summaries
Each task summary in the left workspace SHALL use direct user-facing information such as title, status, creation time, and issue count and SHALL NOT require users to interpret hashes, snapshot identifiers, or connector versions.

#### Scenario: Task metadata contains internal identifiers
- **WHEN** a historical task includes file hashes, snapshot IDs, or internal source versions
- **THEN** the left workspace omits those values from its summary while retaining them in backend records

### Requirement: Preserve predictable return navigation
The Web application SHALL preserve browser-back and page-back behavior after users enter tasks and difference details from the left workspace.

#### Scenario: User returns from a person's issue detail
- **WHEN** the user activates the page back control after drilling into a task's difference details
- **THEN** the application returns to the immediately preceding task context, or to the task list when no valid history entry exists

### Requirement: Adapt the workspace to available width
The Web application SHALL provide an expanded and collapsed desktop sidebar and an accessible drawer on narrow screens without obscuring primary content or actions.

#### Scenario: User collapses the desktop sidebar
- **WHEN** the user activates the sidebar collapse control on a desktop viewport
- **THEN** the sidebar switches to a stable icon-width layout and the main content uses the released width without overlapping controls

#### Scenario: User selects a task from the mobile drawer
- **WHEN** the user opens the workspace drawer on a narrow viewport and selects a history item
- **THEN** the application navigates to the task, closes the drawer, and restores focus to a logical control in the destination view

### Requirement: Keep connection status available
The left workspace SHALL display backend connection status at its bottom and SHALL preserve the existing retry behavior when a health check fails.

#### Scenario: Backend health check fails
- **WHEN** the connection check reports an unavailable backend
- **THEN** the workspace shows an offline state and provides the existing retry action without blocking navigation to locally available history

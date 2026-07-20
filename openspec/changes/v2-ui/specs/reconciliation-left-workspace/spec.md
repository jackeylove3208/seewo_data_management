## MODIFIED Requirements

### Requirement: Start a new reconciliation from the workspace
The left workspace SHALL provide separate, visually prominent “新建对话” and “外部数据同步” commands. “新建对话” SHALL open a fresh conversational draft session, while “外部数据同步” SHALL open the manual external-data-sync entry without mutating any historical task.

#### Scenario: User starts a new conversation
- **WHEN** the user selects “新建对话” while viewing any reconciliation route
- **THEN** the application opens a new empty conversational draft session and marks “新建对话” as current

#### Scenario: User starts external data sync
- **WHEN** the user selects “外部数据同步” while viewing any reconciliation route
- **THEN** the application opens the initial manual-sync entry and marks “外部数据同步” as current

#### Scenario: User leaves a historical task
- **WHEN** the user selects either primary command while viewing a historical task
- **THEN** the application does not mutate the historical task or its persisted selections

## ADDED Requirements

### Requirement: Preserve primary navigation clarity at every width
The left workspace SHALL keep “新建对话” and “外部数据同步” distinguishable, directly named, and accessible in expanded desktop, collapsed desktop, and mobile-drawer states.

#### Scenario: User collapses the workspace
- **WHEN** the sidebar enters its compact desktop state
- **THEN** each primary command retains a distinct familiar icon, an accessible name, and a tooltip without changing the main-content dimensions on hover

#### Scenario: User uses the mobile drawer
- **WHEN** the user activates either primary command from the mobile workspace drawer
- **THEN** the application navigates to the selected destination and closes the drawer without obscuring the destination heading


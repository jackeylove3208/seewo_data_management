## ADDED Requirements

### Requirement: Create a task draft through a conversation
The Web application SHALL provide an AI-style conversation that collects reconciliation scope, entity types, processing mode, and a task title into an independent structured draft without collecting CSV files or creating a reconciliation task.

#### Scenario: User describes a reconciliation goal
- **WHEN** the user describes a school scope and the entities to reconcile
- **THEN** the assistant updates the structured draft and asks only for required task information that remains missing

#### Scenario: Conversation draft is displayed
- **WHEN** the assistant has recognized any task information
- **THEN** the conversation presents the current task draft separately from chat text without showing CSV selectors

### Requirement: Hand off a valid conversational draft
The conversation SHALL provide an explicit command that transfers a valid task draft to “外部数据同步” for CSV selection and task creation.

#### Scenario: Task draft becomes valid
- **WHEN** title, scope, entity types, and processing mode are valid
- **THEN** the conversation enables the handoff command and identifies external data sync as the next step

#### Scenario: User continues to external data sync
- **WHEN** the user activates the handoff command
- **THEN** the application opens manual external data sync with the exact draft values available for editing

#### Scenario: Task draft is incomplete
- **WHEN** any required non-file task field is absent or invalid
- **THEN** the handoff command remains unavailable and the assistant identifies the missing information

### Requirement: Recover from assistant failures without losing the draft
The conversation SHALL preserve recognized task information when assistant response validation or processing fails and SHALL allow the user to retry or edit the draft.

#### Scenario: Assistant output is invalid
- **WHEN** the assistant adapter returns an invalid structured response
- **THEN** the conversation displays a recoverable error, retains the prior draft, and allows another message or direct field correction

## MODIFIED Requirements

### Requirement: Maintain a structured task draft
The application SHALL store recognized title, scope, entity types, and processing mode in a structured draft independent of rendered chat text, SHALL keep that draft editable, and SHALL validate it before enabling handoff to external data sync.

#### Scenario: Assistant output omits a required field
- **WHEN** an assistant response does not provide all required non-file task fields
- **THEN** the application keeps the draft non-transferable and identifies the next missing field through a follow-up prompt or editable draft field

#### Scenario: User edits recognized information
- **WHEN** the user changes a task title, scope, entity selection, or processing mode before handoff
- **THEN** the structured draft reflects the edited value and transfers that latest value to external data sync

## REMOVED Requirements

### Requirement: Create reconciliation tasks through a conversation
**Reason**: V2 separates goal definition from external-data ingestion, so the conversation produces a draft instead of creating a task.

**Migration**: Use “新建对话” to produce the draft, then hand it off to “外部数据同步” for CSV selection and task creation.

### Requirement: Accept task data attachments
**Reason**: CSV selection belongs exclusively to the manual external-data-sync workflow.

**Migration**: Select and validate both CSV files after entering “外部数据同步” and activating “手动同步”.

### Requirement: Require explicit task confirmation
**Reason**: The conversation no longer owns final task creation or file-backed confirmation.

**Migration**: Confirm the non-file draft through the handoff command, then use “开始同步” on the valid external-data-sync form as the explicit task-creation boundary.

### Requirement: Reuse the governed task creation service
**Reason**: The existing governed task creation service remains in use but moves from the conversation page to external data sync.

**Migration**: External data sync submits the handed-off or default draft through the unchanged service.

### Requirement: Recover from assistant and submission failures
**Reason**: Assistant recovery and task-submission recovery now occur on separate pages.

**Migration**: The conversation preserves drafts after assistant failures; external data sync preserves files and task information after upload or task-creation failures.

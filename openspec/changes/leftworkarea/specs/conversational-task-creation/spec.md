## ADDED Requirements

### Requirement: Create reconciliation tasks through a conversation
The Web application SHALL provide an AI-style conversation as the primary new-reconciliation experience and SHALL collect the data source, reconciliation scope, entity types, and processing mode required by the existing task creation workflow.

#### Scenario: User describes a reconciliation goal
- **WHEN** the user enters a goal such as reconciling teachers and students for a selected school scope
- **THEN** the assistant records recognized values in a structured task draft and asks only for required information that remains missing

### Requirement: Accept task data attachments
The conversation SHALL require one third-party CSV and one Seewo CSV in the current demo ingestion mode and SHALL show readable file names, validation state, and row summaries.

#### Scenario: User attaches a valid CSV
- **WHEN** the selected file can be summarized and passes client-side checks
- **THEN** the conversation associates the attachment with the task draft and shows its direct data summary without exposing hashes

#### Scenario: Only one demo file is ready
- **WHEN** either the third-party CSV or the Seewo CSV is absent or invalid
- **THEN** the conversation keeps “创建对账” disabled and preserves the valid attachment

#### Scenario: Attached file is invalid
- **WHEN** the selected file cannot be read or fails a required client-side check
- **THEN** the conversation identifies the affected attachment, preserves other draft fields, and allows the user to replace it

### Requirement: Maintain a structured task draft
The application SHALL store recognized task values in a structured draft independent of rendered chat text and SHALL validate the draft before enabling task creation.

#### Scenario: Assistant output omits a required field
- **WHEN** an assistant response does not provide all required task fields
- **THEN** the application keeps the draft non-creatable and identifies the next missing field through a follow-up prompt or editable confirmation field

### Requirement: Require explicit task confirmation
The application SHALL present the complete task draft for review and SHALL create a reconciliation task only after the user activates an explicit confirmation command.

#### Scenario: Draft becomes valid
- **WHEN** all required task fields and attachments are valid
- **THEN** the application displays the title, scope, selected entity types, mode, and attachment summary and enables “创建对账”

#### Scenario: User changes a confirmed field
- **WHEN** the user edits a draft field before submission
- **THEN** the application updates the draft summary and requires confirmation of the updated values

### Requirement: Reuse the governed task creation service
The conversation SHALL submit a confirmed draft through the existing upload and reconciliation-task creation service and SHALL NOT construct governance execution or rollback commands.

#### Scenario: Task creation succeeds
- **WHEN** the confirmed draft is accepted by the existing task creation service
- **THEN** the application adds the task to history and navigates to its task detail where deterministic processing continues

#### Scenario: User asks the assistant to fix or roll back data
- **WHEN** a user requests direct governance execution or rollback in the creation conversation
- **THEN** the assistant does not execute the request and instead directs the user to the applicable reviewed workflow

### Requirement: Recover from assistant and submission failures
The conversation SHALL preserve valid user inputs and attachments when assistant output validation or task submission fails and SHALL provide a retry path.

#### Scenario: Assistant output is invalid
- **WHEN** the assistant adapter returns an invalid structured response
- **THEN** the application displays a recoverable error, retains the draft, and allows the user to retry or edit required fields manually

#### Scenario: Backend task creation fails
- **WHEN** the existing task creation service rejects or fails the confirmed request
- **THEN** the application remains on the draft, displays the server message in user-facing language, and allows a safe retry without creating duplicate tasks

### Requirement: Isolate the model implementation
The conversation SHALL consume a validated assistant adapter contract so that a deterministic test adapter and a future small-model backend can be substituted without changing task draft or submission behavior.

#### Scenario: Assistant adapter is replaced
- **WHEN** the configured assistant implementation changes from the deterministic adapter to a model-backed adapter
- **THEN** the conversation continues to accept the same validated assistant response shape and preserves the explicit confirmation boundary

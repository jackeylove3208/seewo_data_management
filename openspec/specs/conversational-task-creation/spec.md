# Conversational task creation Specification

## Purpose

Define the durable backend-owned Agent conversation and its task lifecycle.
## Requirements
### Requirement: Provide an Agent-only conversation
The Web application SHALL provide a persistent AI-style conversation that renders Agent/user messages and typed task cards without a task-draft side panel, SHALL obtain messages from backend conversation APIs, and SHALL allow one conversation to manage multiple sequential sync or rollback tasks.

#### Scenario: User opens a new conversation
- **WHEN** the user navigates to “新建对话”
- **THEN** the page shows the persistent Agent conversation and composer without a visible “任务草案” region

#### Scenario: User describes a synchronization goal
- **WHEN** the backend Agent recognizes source, target, whole-school scope, and selected entity types
- **THEN** it posts a start-confirmation card and does not create or lock a task until the user activates start

### Requirement: Maintain private multi-turn context
The conversation SHALL retain validated intent and prior task/event context on the backend, SHALL bind it to authenticated tenant and conversation identity, and SHALL NOT expose hidden prompts, credentials, or client-editable workflow state.

#### Scenario: User refines a request before start
- **WHEN** a later message changes entity types or connector selection
- **THEN** the Agent updates private intent and produces a new versioned start confirmation

#### Scenario: User completes one task
- **WHEN** a report-complete or terminated task releases the school lock
- **THEN** the same conversation re-enables free input and may prepare the next task

### Requirement: Recover from assistant failures
The conversation SHALL preserve persisted context and events when response validation or model processing fails, SHALL perform no more than three retries after the initial model call, and SHALL show a sanitized terminal model-error card when retries are exhausted.

#### Scenario: Model output remains invalid
- **WHEN** the initial attempt and three retries fail
- **THEN** the task stays locked and blocked, prior work remains intact, the composer stays disabled, and a terminate command is displayed

#### Scenario: Pre-task assistant response fails
- **WHEN** no task/lock exists and intent processing fails
- **THEN** the conversation preserves prior validated intent and permits another user message

### Requirement: Use the available conversation workspace
The conversation page SHALL use the available viewport, keep the composer or current task controls visible, scroll internally, and render start, progress, approval, clarification, error, report, and rollback cards without horizontal overflow.

#### Scenario: Task is waiting for ordinary high-risk approval
- **WHEN** a grouped approval event is current
- **THEN** the composer remains disabled and the card exposes pageable details plus agree, reject, and terminate controls

#### Scenario: Task is waiting for identity clarification
- **WHEN** a conflict batch is current
- **THEN** the composer is temporarily enabled for that batch and the interpreted decision is shown for confirm or restate

### Requirement: Convert a chat link into a private validated intent reference
The conversation SHALL replace a submitted URL with a cleaned origin marker before persisting the
display message or constructing model input, SHALL expose the server-registered remote resource as
a trusted context item, and SHALL accept only a resource registered to the same tenant, operator,
and conversation in a start confirmation.

#### Scenario: Model receives a link-bearing message
- **WHEN** the backend has registered the message URL
- **THEN** the model receives a safe origin marker and `remote_source_id` but not the original URL or query string

#### Scenario: Model invents or reuses another conversation resource
- **WHEN** a decision references a remote source not registered to the current conversation
- **THEN** the backend rejects the selection and produces a safe clarification without creating a task

#### Scenario: User confirms a remote source with a Seewo target
- **WHEN** entity types, one conversation-bound remote authoritative source, and one server-listed local Seewo target are complete
- **THEN** the conversation produces the existing start-confirmation card and waits for the user start command

### Requirement: Configure and select organization API connections safely
The conversation SHALL identify a registered organization provider, list only safe tenant-owned
connection views, require a new conversation-bound configuration session for every organization
API task, and create an API-authority/database-target intent only after the task-scoped connection
capability, visibility, entity selection, and target validation succeed.

#### Scenario: User asks to synchronize DingTalk
- **WHEN** the user names DingTalk, including when reusable tenant connections already exist
- **THEN** the Agent presents a fresh secure-configuration action without requesting the
  application secret in conversation or selecting a historical connection

#### Scenario: User configures a task-scoped connection
- **WHEN** the secure card is opened for a new DingTalk task
- **THEN** it supplies an editable generated connection name and requires the user to resubmit the
  organization scope, entity configuration, AppKey, and AppSecret without copying values from a
  previous task

#### Scenario: User selects a tested connection
- **WHEN** a current-conversation task-scoped connection has current required capabilities and
  non-empty visibility
- **THEN** the conversation stores only its connection ID, provider ID, safe display name, selected
  entities, and target reference in private intent and cannot select persistent, cross-conversation,
  already-bound, or revoked connections

#### Scenario: User confirms task start
- **WHEN** the API authority, MySQL target, whole-school scope, and selected entities are valid
- **THEN** one idempotent Agent task is created and no credential or access token is copied into the
  task, run, Graph, or model context

### Requirement: Render safe API connector status and errors
The conversation SHALL render typed connection configuration, test, capability, visibility, and
sanitized failure states without displaying provider response bodies, headers, tokens, secrets, or
internal stack traces.

#### Scenario: Provider denies contact permission
- **WHEN** connection testing returns `connector_permission_denied`
- **THEN** the conversation explains that application permission or visibility must be corrected
  and offers a retry after configuration changes

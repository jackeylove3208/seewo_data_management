## ADDED Requirements

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

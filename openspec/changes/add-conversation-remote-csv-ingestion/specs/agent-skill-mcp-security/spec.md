## ADDED Requirements

### Requirement: Keep remote network authority outside every model tool boundary
The Agent runtime SHALL keep the original remote URL and network client outside conversation-model,
supervisor, sub-agent, and MCP inputs, and SHALL expose only materialized task-bound resource IDs
through evidence manifests.

#### Scenario: Model tool call includes a URL
- **WHEN** any model-facing tool call supplies `url`, `path`, `dsn`, `sql`, credentials, or an unbound resource ID
- **THEN** the MCP authorization layer rejects and safely audits the call before resource access

#### Scenario: Source-understanding Skill reads evidence
- **WHEN** the fixed-field mapping is ambiguous
- **THEN** the Skill can inspect metadata and at most fifty tokenized rows from the current materialized `SourceFile` without receiving network authority

#### Scenario: Remote cell contains prompt instructions
- **WHEN** a CSV cell attempts to direct the model or override its role
- **THEN** the content remains untrusted evidence and cannot expand tools, fields, resources, or task scope

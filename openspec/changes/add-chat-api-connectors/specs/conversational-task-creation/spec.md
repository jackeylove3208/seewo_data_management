## ADDED Requirements

### Requirement: Configure and select organization API connections safely
The conversation SHALL identify a registered organization provider, list only safe tenant-owned
connection views, direct missing credentials to a one-time secure configuration session, and create
an API-authority/database-target intent only after connection capability, visibility, entity
selection, and target validation succeed.

#### Scenario: User asks to synchronize DingTalk
- **WHEN** the user names DingTalk and no active tenant connection is available
- **THEN** the Agent presents a secure-configuration action without requesting the application
  secret in conversation

#### Scenario: User selects a tested connection
- **WHEN** a tenant-owned connection has current required capabilities and non-empty visibility
- **THEN** the conversation stores only its connection ID, provider ID, safe display name, selected
  entities, and target reference in private intent

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

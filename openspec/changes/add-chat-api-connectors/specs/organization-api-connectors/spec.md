## ADDED Requirements

### Requirement: Register audited organization API providers
The system SHALL load versioned provider manifests that bind a provider identifier to one backend
Adapter, fixed endpoint policy, credential schema, supported organization entities, required
capabilities, pagination limits, and field-projection version.

#### Scenario: Conversation selects DingTalk
- **WHEN** an authenticated tenant selects the registered DingTalk provider
- **THEN** the backend resolves the audited DingTalk Adapter and does not ask a model to discover or
  construct an endpoint

#### Scenario: Entity is unsupported
- **WHEN** a connection cannot deterministically provide one selected organization entity
- **THEN** task creation is rejected with a sanitized capability error before acquiring a school
  lock

### Requirement: Configure tenant connections without exposing credentials
The system SHALL store tenant-scoped public connection configuration separately from an opaque
secret reference and SHALL allow only the backend provider runtime to resolve the secret.

#### Scenario: User submits an application secret
- **WHEN** the user submits provider credentials through a one-time secure configuration session
- **THEN** the secret is stored by the secret backend and is absent from conversation messages,
  task intent, model payloads, Skill/MCP arguments, checkpoints, events, logs, and ordinary
  connection responses

#### Scenario: Tenant reads another tenant connection
- **WHEN** an authenticated tenant requests or references a connection owned by another tenant
- **THEN** the backend returns not found or forbidden without disclosing connection metadata

### Requirement: Test connection capabilities and visibility safely
The system SHALL test authentication, required read capabilities, selected-entity support, and
organization visibility without creating a reconciliation task or target mutation and SHALL persist
only sanitized results.

#### Scenario: Credentials are valid but visibility is empty
- **WHEN** provider authentication succeeds but the application can read no organization records
- **THEN** the connection is not eligible for synchronization and reports
  `connector_visibility_empty`

#### Scenario: Provider rejects permission
- **WHEN** the provider returns a permission error during the minimum capability probe
- **THEN** the backend records `connector_permission_denied` without returning the provider response
  body, token, request headers, or secret

### Requirement: Materialize complete immutable API authority evidence
For an API authority task, the system SHALL capture every selected provider page after the school
lock is acquired, validate pagination closure and external-ID uniqueness, and atomically publish one
task-bound managed source file and authoritative snapshot before source inspection.

#### Scenario: Capture succeeds
- **WHEN** all selected organization resources reach a valid terminal cursor
- **THEN** the published snapshot records task, tenant, connection, selection hash, content hash,
  record/page counts, provider manifest version, Adapter version, and projection version

#### Scenario: Capture fails halfway
- **WHEN** a cursor repeats, an external ID conflicts, permission changes, or a page cannot be
  completed
- **THEN** no partial artifact is published and the Graph cannot advance to source inspection

#### Scenario: Completed action is retried
- **WHEN** the same task, source binding, selection, and Adapter contract retry materialization
- **THEN** the backend returns the same published source and snapshot without another mutable
  authority version

### Requirement: Implement providers through one deterministic Adapter contract
DingTalk and WeCom organization integrations SHALL implement the same backend Adapter contract for
connection testing, capability inspection, immutable capture, sanitized error translation, and
six-field projection while retaining provider-specific authentication and pagination internally.

#### Scenario: Second provider is registered
- **WHEN** WeCom support is enabled after DingTalk
- **THEN** it uses the existing connection, materialization, ingestion, identity, and Graph contracts
  without adding a provider-specific graph node or Skill

## ADDED Requirements

### Requirement: Register one public CSV link from a conversation message
The system SHALL activate remote-source registration only when an authenticated user sends exactly
one HTTPS URL in an Agent conversation message, SHALL bind the resulting resource to the tenant,
operator, and conversation, and SHALL perform no network download before task start.

#### Scenario: Conversation message contains one HTTPS link
- **WHEN** the user sends one syntactically valid HTTPS URL in an active Agent conversation
- **THEN** the backend registers one `remote_source_id` for that conversation and continues intent recognition with a safe source summary

#### Scenario: Conversation message has no link
- **WHEN** the user sends an ordinary chat message without a URL
- **THEN** the existing conversation behavior runs without creating a remote source

#### Scenario: Conversation message contains multiple links
- **WHEN** one message contains more than one HTTP or HTTPS URL
- **THEN** the backend asks the user to send one source link and registers none

### Requirement: Materialize a safe immutable remote CSV snapshot
The system SHALL allow only HTTPS destinations that resolve and connect to public network addresses,
SHALL validate every redirect, SHALL enforce bounded redirects, time, and bytes, SHALL reject
non-CSV content using deterministic inspection, and SHALL publish a `SourceFile` only after the
complete content is stored and hashed.

#### Scenario: Public HTTPS CSV is valid
- **WHEN** the Graph materialization action downloads a valid CSV within configured limits
- **THEN** it persists one immutable authoritative `SourceFile`, snapshot provenance, content hash, and retrieval metadata for the task

#### Scenario: Destination reaches a forbidden address
- **WHEN** the initial host or any redirect resolves or connects to loopback, private, link-local, multicast, reserved, or metadata address space
- **THEN** materialization stops with a sanitized network-policy failure and publishes no `SourceFile`

#### Scenario: Response is not a valid bounded CSV
- **WHEN** the response is empty, oversized, HTML, JSON, spreadsheet, archive, or cannot be inspected as CSV
- **THEN** materialization records a typed safe failure and source inspection does not start

### Requirement: Reuse one task snapshot across retries
The system SHALL make remote materialization idempotent for a task and SHALL use the same completed
content hash for all later task retries and recovery.

#### Scenario: Worker resumes after completed download
- **WHEN** complete stored content exists for the action idempotency key but its transition was interrupted
- **THEN** the worker reuses that content and does not request the remote URL again

#### Scenario: Remote content changes after materialization
- **WHEN** the public URL returns different content after the task snapshot was published
- **THEN** the current task continues to use its original immutable snapshot

### Requirement: Report remote failures without leaking source details
The system SHALL expose stable safe problem codes and a cleaned source origin while excluding the
full URL, query string, response body, credentials, and raw protected rows from client errors,
model traces, reports, and ordinary logs.

#### Scenario: Remote request fails
- **WHEN** DNS, connection, redirect, timeout, size, or content validation fails
- **THEN** the task records the matching safe problem code and no response body or full URL is returned to the client

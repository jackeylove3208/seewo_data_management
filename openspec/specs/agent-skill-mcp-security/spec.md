# Agent Skill MCP security Specification

## Purpose

Define bounded Skill loading, MCP authorization, privacy, and safe model provenance.

## Requirements

### Requirement: Load versioned phase-specific Skills
The system SHALL bind every supervisor and sub-agent invocation to a named Skill version, strict input/output schema, phase, and allowed MCP capability set.

#### Scenario: Skill requests a tool outside its phase
- **WHEN** an analysis Skill requests a connector write or school-lock mutation tool
- **THEN** authorization rejects the call before model arguments reach that tool

#### Scenario: Historical output is inspected
- **WHEN** an operator opens prior Agent evidence
- **THEN** the API exposes the immutable Skill/model/provenance version used rather than rerunning the latest Skill

### Requirement: Authorize every MCP call with server-owned context
Every MCP call SHALL validate tenant, conversation, task, run, phase, snapshot pair, resource IDs, and approval/plan version where applicable and SHALL return not found or forbidden without cross-tenant disclosure.

#### Scenario: Model invents a candidate ID
- **WHEN** a response or tool call references an entity outside the current work item's evidence manifest
- **THEN** the backend rejects it and creates no correspondence, finding, approval, or operation

#### Scenario: Execution tool lacks approval
- **WHEN** a high-risk operation is requested without the exact persisted approval group/version
- **THEN** the execution tool refuses the request

### Requirement: Prohibit generic model-controlled infrastructure access
The Agent runtime SHALL NOT expose generic SQL execution, arbitrary file paths, arbitrary URLs, connector credentials, shell commands, or unconstrained mutation tools to any model.

#### Scenario: Skill text requests arbitrary SQL
- **WHEN** a model attempts to call or synthesize an unavailable generic database tool
- **THEN** the request fails as an unsafe tool call and the attempt is audited safely

### Requirement: Protect student phone at every model boundary
For new Agent tasks, the system SHALL tokenize `student.phone` with a deterministic task-scoped token before an external model or model-facing MCP payload, SHALL mask it in ordinary UI/reports, SHALL omit it from logs/traces, and SHALL detokenize only known evidence for an approved operation.

#### Scenario: Student pair is analyzed
- **WHEN** a batch includes authoritative and Seewo student phone values
- **THEN** DeepSeek receives typed task-scoped tokens and no raw student phone

#### Scenario: Model invents a phone token
- **WHEN** model output contains a token that was not issued in its authorized evidence
- **THEN** validation rejects the output and no target value is changed

### Requirement: Persist safe provenance and sanitized errors
The system SHALL persist provider, model, Skill/prompt version, batch/input/output hashes, tool trace IDs, token usage, gateway request IDs, attempt count, and timestamps without raw prompts, credentials, protected payloads, or stack traces.

#### Scenario: Retries are exhausted
- **WHEN** a model batch fails after the initial attempt and three retries
- **THEN** the conversation receives a stable sanitized error event and the full sensitive backend exception remains inaccessible to clients

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

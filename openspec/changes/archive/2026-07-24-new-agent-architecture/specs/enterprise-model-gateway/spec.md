## MODIFIED Requirements

### Requirement: Configure an OpenAI-compatible enterprise gateway
The backend SHALL use validated server-side DeepSeek/OpenAI-compatible configuration for supervisor and sub-agent reasoning, including endpoint, API key, model, authentication, response mode, timeouts, extra parameters, and a policy of one initial attempt plus at most three retries.

#### Scenario: Agent batch requires reasoning
- **WHEN** a versioned Skill receives a bounded evidence manifest
- **THEN** the provider calls the configured model and validates its strict local output schema

#### Scenario: Gateway is unavailable
- **WHEN** the initial attempt and three retries fail
- **THEN** the run blocks, posts a sanitized error, preserves the school lock, and does not substitute a fake analysis

### Requirement: Tokenize every model-visible sensitive value
For `new-agent-v1`, the system SHALL tokenize student phone before every initial prompt and model-facing MCP result and SHALL retain legacy broader tokenization for historical workflows.

#### Scenario: New student work item is sent
- **WHEN** its evidence contains raw source or target phone
- **THEN** outbound model content contains only the issued task-scoped phone token

#### Scenario: Non-student field is processed
- **WHEN** the new privacy policy does not classify that field as high-sensitivity
- **THEN** model serialization follows the new contract without weakening historical stored-token protections

### Requirement: Restrict detokenization to supplied evidence
The backend SHALL detokenize only tokens issued in the authorized task/work-item context and SHALL reject invented tokens, raw student phone output, or values not supported by authoritative evidence.

#### Scenario: Model returns unknown phone token
- **WHEN** a solution references a token not issued to that invocation
- **THEN** the attempt fails and no finding, approval, proposal, or mutation uses it

### Requirement: Produce versioned multi-option analysis
The system SHALL persist versioned Agent finding analyses with a Chinese category, explanation, evidence summary, risk, one to three structured governance solutions, and a recommended path for every actionable work item.

#### Scenario: Evidence supports an operation
- **WHEN** a solution is compatible with authoritative evidence and server policy
- **THEN** it is persisted with Skill/model provenance and later risk classification

#### Scenario: Identity evidence conflicts
- **WHEN** no safe identity decision exists
- **THEN** analysis exposes the conflict and clarification path rather than an executable candidate

### Requirement: Enforce analysis option policy
Every Agent solution SHALL be validated for finding kind, allowed operation, target/candidate membership, field whitelist, expected before value, authoritative after value, evidence references, risk, connector capability, and current versions.

#### Scenario: Model proposes third-party mutation
- **WHEN** any option targets the authoritative connector
- **THEN** validation rejects it and no client can approve it

### Requirement: Preserve model provenance without sensitive payloads
The system SHALL record provider, model, Skill/prompt version, hashes, tool traces, usage, gateway request IDs, attempt count, and time without raw prompts, credentials, student phone, unrestricted tool payloads, or stack traces.

#### Scenario: Error is shown in conversation
- **WHEN** retries are exhausted
- **THEN** the client sees only stable code, phase/batch, safe request ID, progress, and attempt count


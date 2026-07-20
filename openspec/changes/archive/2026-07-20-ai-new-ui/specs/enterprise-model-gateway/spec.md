## ADDED Requirements

### Requirement: Configure an OpenAI-compatible enterprise gateway
The backend SHALL read the complete model endpoint, secret API key, model identifier, authentication format, response mode, timeout, retry policy, extra headers, and extra request body from validated server-side configuration.

#### Scenario: Gateway is fully configured
- **WHEN** a semantic difference requires model reasoning
- **THEN** the provider sends a real HTTPS request to the configured endpoint using the configured model and authentication

#### Scenario: Gateway is not configured
- **WHEN** a semantic difference requires model reasoning but the URL, API key, model, or tokenization secret is missing
- **THEN** the backend records a stable configuration failure and routes the difference to manual handling without pretending a model was called

### Requirement: Keep model secrets on the backend
The system SHALL load real model secrets from ignored environment configuration or a deployment secret store and SHALL never expose them through frontend bundles, API responses, readiness details, or logs.

#### Scenario: Readiness is requested
- **WHEN** a client calls the readiness endpoint
- **THEN** the response may report whether the provider is configured but does not return credentials, headers, or extra request values

### Requirement: Validate configurable extra parameters
The backend SHALL parse extra headers and extra request parameters as size-bounded JSON objects and SHALL reject values that override reserved protocol fields.

#### Scenario: Safe enterprise parameter is configured
- **WHEN** the extra request body contains a provider-specific parameter such as `top_p`
- **THEN** the provider merges it into the request sent to the enterprise gateway

#### Scenario: Extra body attempts to replace messages
- **WHEN** configuration attempts to override `model`, `messages`, `response_format`, or another reserved field
- **THEN** configuration validation fails before a model request is sent

### Requirement: Support gateway response compatibility modes
The provider SHALL support JSON Schema, JSON object, and prompt-enforced JSON response modes while applying the same local schema and policy validation to every mode.

#### Scenario: Gateway lacks JSON Schema support
- **WHEN** the configured response mode is JSON object or prompt-enforced JSON
- **THEN** the provider omits unsupported protocol fields, parses the returned JSON, and validates the result locally

### Requirement: Tokenize every model-visible sensitive value
The system SHALL replace personal names, phone numbers, email addresses, and external source identifiers with stable task-scoped HMAC tokens before they enter an initial prompt or MCP tool result.

#### Scenario: Initial difference context is sent
- **WHEN** the Agent builds model messages for a teacher or student difference
- **THEN** the outbound payload contains stable typed tokens and contains none of the original protected values

#### Scenario: Agent requests MCP context
- **WHEN** an allowed MCP tool returns candidates or difference evidence
- **THEN** the tool result passes through the same task tokenization context before being appended to model messages

### Requirement: Restrict detokenization to supplied evidence
The backend SHALL only detokenize values that were present in the authorized analysis context and SHALL reject invented or unknown sensitive values.

#### Scenario: Model returns a known token
- **WHEN** a proposed change references a token issued for an authoritative snapshot value
- **THEN** the backend resolves it in memory and validates the resulting value against the snapshot evidence

#### Scenario: Model invents a token or contact value
- **WHEN** a response contains an unknown token or a new phone or email value not supported by evidence
- **THEN** the output is rejected and the difference is retried or routed to manual handling

### Requirement: Produce versioned multi-option analysis
The system SHALL persist analysis-v2 with a cause, evidence summary, manual-only state, and zero to three structured governance options bound to the current difference version.

#### Scenario: Multiple safe resolutions exist
- **WHEN** the model returns two or three policy-compliant ways to resolve an ambiguous difference
- **THEN** the backend persists every validated option and marks exactly one as recommended

#### Scenario: Evidence is insufficient or risk is high
- **WHEN** identity, parent mapping, source evidence, or destructive impact cannot support a safe automatic option
- **THEN** analysis-v2 contains no executable option, marks manual-only, and explains the missing evidence or risk

### Requirement: Enforce analysis option policy
Every analysis option SHALL be validated for allowed operation, target membership, field whitelist, expected before value, authoritative after value, evidence references, risk, and confidence before persistence.

#### Scenario: Model recommends a forbidden operation
- **WHEN** an option proposes an operation incompatible with the difference type
- **THEN** the backend rejects the model output and does not expose that option to the user

### Requirement: Preserve model provenance without sensitive payloads
The system SHALL record provider, model, Skill version, prompt version, tool trace identifiers, token usage, gateway request ID, attempt count, and generation time without storing raw prompts, token mappings, credentials, or protected tool payloads.

#### Scenario: Historical analysis is opened
- **WHEN** the user views a persisted analysis-v2 record
- **THEN** the API returns its immutable output and safe provenance rather than regenerating it


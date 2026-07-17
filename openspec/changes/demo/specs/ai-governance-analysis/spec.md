## ADDED Requirements

### Requirement: Require pre-execution analysis
The system SHALL produce a cause, evidence summary, recommended action, risk level, and confidence for every difference before it can be selected for execution.

#### Scenario: Analysis pending
- **WHEN** a difference has no valid analysis result
- **THEN** the system marks it non-executable and exposes the pending or failed analysis state

### Requirement: Use governed agent tools
The analysis Agent SHALL follow versioned Skills and access reconciliation context only through registered read-oriented MCP tools.

#### Scenario: Agent requests candidate context
- **WHEN** an ambiguous difference requires additional match evidence
- **THEN** the Agent uses the candidate-search MCP tool and cannot call target mutation operations

### Requirement: Validate structured model output
The system SHALL validate model responses against Pydantic schemas and business constraints before persisting analysis or plans.

#### Scenario: Invalid model response
- **WHEN** a model omits a required action, risk, confidence, or reason field
- **THEN** the system rejects the response, records the failure, and retries or routes the item to manual review

### Requirement: Record AI provenance
The system SHALL record model provider, model identifier, Skill version, prompt version, tool trace identifiers, and output timestamp.

#### Scenario: Reviewing historical analysis
- **WHEN** a user opens a previously analyzed difference
- **THEN** the system returns the original persisted analysis and provenance rather than silently regenerating it

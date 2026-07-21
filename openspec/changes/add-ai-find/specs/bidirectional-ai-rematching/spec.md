## ADDED Requirements

### Requirement: Index both immutable snapshot roles
The system SHALL maintain embedding records for authoritative and target snapshots, isolated by tenant, snapshot, source role, entity type, entity ID, representation version, provider, and model.

#### Scenario: Paired snapshots are ready for rematching
- **WHEN** initial entity resolution leaves unresolved entities
- **THEN** the system idempotently indexes unresolved searchable entities from both snapshots before candidate retrieval

#### Scenario: Another tenant has similar data
- **WHEN** a vector query is executed for one tenant
- **THEN** embeddings and candidates from every other tenant are excluded

### Requirement: Retrieve candidates in both directions
The system SHALL query each unresolved authoritative entity against the target index and each unconsumed target entity against the authoritative index, using a default Top-K of 3 and deduplicating the resulting candidate edges.

#### Scenario: Apparent Seewo-redundant student has a source counterpart
- **WHEN** a target student is unconsumed after initial matching
- **THEN** reverse retrieval searches the authoritative student index and can recover a candidate that source-to-target blocking missed

#### Scenario: Candidate volume is large
- **WHEN** paired snapshots contain hundreds or thousands of entities
- **THEN** the system never sends the full cross-product to the model and persists at most the configured bounded candidate set per focal entity

### Requirement: Preserve accepted initial mappings
The system SHALL exclude current accepted mappings with valid evidence from AI rematching and SHALL process only manual-review, conflict, unmatched, and unconsumed-target entities.

#### Scenario: Deterministic student match is already accepted
- **WHEN** a student was uniquely matched by an allowed key group
- **THEN** no rematching work item or model request is created for that student

### Requirement: LLM decisions are restricted to server candidates
The LLM MUST return a structured decision of accept-candidate, no-match, or manual-review and MUST NOT select an entity ID outside the server-owned candidate set.

#### Scenario: Model invents a target ID
- **WHEN** the model returns an ID that was not included in the persisted Top-3 candidate set
- **THEN** the output is rejected and the entity receives a safe manual-review result

#### Scenario: Evidence is insufficient
- **WHEN** the Top-3 candidates remain ambiguous or contain conflicting identity evidence
- **THEN** the model returns manual-review with concise Chinese reasons rather than inventing a match

### Requirement: Rematching uses governed model access
All model-assisted rematching SHALL use the configured enterprise gateway, task-scoped tokenization, structured schema validation, bounded retries, Chinese business explanations, and non-sensitive provenance.

#### Scenario: Gateway receives student evidence
- **WHEN** a student candidate set is sent for adjudication
- **THEN** model-visible protected values are tokenized and raw provider logs are not persisted

#### Scenario: Gateway is unavailable
- **WHEN** bounded gateway retries are exhausted
- **THEN** deterministic matches remain committed and affected unresolved entities receive auditable manual-review fallbacks

### Requirement: Rematching is durable and independently observable
The system SHALL persist rematching jobs, work items, candidate edges, attempts, leases, outcomes, and counters so processing survives browser refresh, API disconnect, and worker restart.

#### Scenario: Worker stops after candidate retrieval
- **WHEN** a lease expires before the LLM decision is committed
- **THEN** another worker can reclaim the item without duplicating a current mapping decision

#### Scenario: User reopens a running task
- **WHEN** rematching is still running
- **THEN** the task response reports initial unresolved, indexed, processed, AI-recovered, manual, conflict, and failed counts from persisted state

### Requirement: Auto-accept requires corroborated high confidence
The system SHALL auto-accept an AI candidate only when the candidate is unique under the global assignment, model confidence meets the configured high-confidence threshold, and at least two independent strong evidence features corroborate the identity.

#### Scenario: Only semantic name similarity exists
- **WHEN** a candidate has a similar name but no second strong feature
- **THEN** the result cannot be auto-accepted and is routed to manual review

#### Scenario: Name and contact evidence agree
- **WHEN** the recommended candidate has unique matching name and contact evidence, passes policy validation, and wins the global assignment
- **THEN** the system may persist an accepted AI-assisted mapping

### Requirement: Rematching never writes source systems
AI-assisted rematching SHALL only create mapping decisions and SHALL NOT modify either snapshot, CSV file, third-party system, or Seewo system.

#### Scenario: AI accepts a recovered pair
- **WHEN** a second-pass candidate is accepted
- **THEN** only mapping history and rematching audit records change

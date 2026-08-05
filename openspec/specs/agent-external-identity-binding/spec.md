# agent-external-identity-binding Specification

## Purpose
TBD - created by archiving change add-chat-api-connectors. Update Purpose after archive.
## Requirements
### Requirement: Persist explicit external identity bindings
The system SHALL persist tenant-, provider-, connection-, and entity-scoped active bindings from one
API authority stable locator to one target connector stable locator with version, evidence hash,
confirming operator, confirmation time, and revocation audit.

#### Scenario: Operator confirms a binding
- **WHEN** an authenticated operator confirms one listed authority locator and one listed target
  locator
- **THEN** the backend creates one versioned active binding without copying the provider technical
  ID into number, phone, or email

#### Scenario: Competing active binding exists
- **WHEN** either locator is already actively bound to a different locator in the same scope
- **THEN** the backend rejects the new binding and preserves the existing audited relationship

### Requirement: Apply valid bindings before ordinary identity lookup
The Agent identity builder SHALL validate active external bindings against current authoritative and
target Agent inputs before building unmatched number, phone, and email postings and SHALL represent
an accepted binding with the normal per-run identity claim.

#### Scenario: Valid binding resolves a record without ordinary keys
- **WHEN** an authoritative API record has no number, phone, or email but has an active binding to
  one current target record
- **THEN** the identity builder creates one `AgentIdentityClaimRecord` and continues ordinary field
  comparison without asking a model to choose an identity

#### Scenario: Ordinary and external evidence disagree
- **WHEN** an active binding points to one target but ordinary identity postings uniquely identify a
  different target
- **THEN** the identity builder creates a deterministic identity conflict and neither target is
  silently mutated

### Requirement: Reject stale or missing external identity evidence safely
The system SHALL NOT apply a binding whose connection, provider, entity kind, authority locator, or
target locator no longer matches the current run and SHALL preserve a safe exception fact for
reporting.

#### Scenario: Target primary key no longer exists
- **WHEN** a binding references a target stable locator absent from the current target snapshot
- **THEN** the binding is reported stale and no identity claim or target operation is created from it

#### Scenario: Authority has no identity evidence
- **WHEN** an API authority record has no valid external binding and no number, phone, or email
- **THEN** identity construction records `authority_identity_absent`, creates an
  `authority_invalid` work item, and does not match by name or provider userid


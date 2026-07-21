## ADDED Requirements

### Requirement: Every analysis has at least one resolution path
Every persisted `analysis-v3` output SHALL contain between one and three resolution paths and SHALL mark exactly one path as recommended.

#### Scenario: Evidence supports a safe update
- **WHEN** authoritative evidence, target identity, field policy, before value, and after value are all valid
- **THEN** the analysis may include an `auto_executable` path containing a validated action

#### Scenario: Evidence is incomplete
- **WHEN** the system cannot determine a safe mutation because required evidence is missing
- **THEN** the analysis includes a `needs_information` path with concrete questions, reasons, and suggested information sources

#### Scenario: Risk prohibits automatic action
- **WHEN** identity, parent relationship, destructive impact, or policy risk prohibits an executable option
- **THEN** the analysis includes a `manual_only` path with ordered, actionable manual steps and no executable mutation

### Requirement: User-visible analysis is Simplified Chinese
The system MUST produce Simplified Chinese for issue title, cause, evidence summary, business impact, solution title, rationale, risk reason, information requests, and manual steps.

#### Scenario: Model returns English content
- **WHEN** model output fails Chinese readability validation
- **THEN** the system rejects it, performs at most one corrective model attempt with a stable validation category, and does not persist the invalid output as successful analysis

#### Scenario: Corrective attempt remains invalid
- **WHEN** the corrective attempt still contains invalid or technical user-visible content
- **THEN** the system persists a deterministic Chinese manual resolution path instead

### Requirement: Stable codes are localized outside the model
The system SHALL keep operation, field, entity, status, and risk identifiers as stable machine codes and SHALL map them to Chinese labels in backend response projections and frontend presentation.

#### Scenario: Update phone recommendation
- **WHEN** an analysis action contains operation code `update` and field code `phone`
- **THEN** the business UI displays `更新` and `手机号` rather than the raw codes

#### Scenario: Diagnostic failure code exists
- **WHEN** a gateway or policy failure code is recorded
- **THEN** normal business text excludes the code and raw provider details while authorized diagnostics may expose the stable code separately

### Requirement: Executable paths remain policy-bound
An `auto_executable` path MUST reference the current difference version, an allowed target entity, permitted operation and fields, matching before values, authoritative after values, valid evidence references, and low or medium risk.

#### Scenario: Model invents a value
- **WHEN** an after value or token cannot be traced to current authoritative evidence
- **THEN** the executable path is rejected and replaced by a safe non-executable resolution path

#### Scenario: Model proposes high-risk action
- **WHEN** a model marks an otherwise structured action as high risk
- **THEN** the action is ineligible for proposal creation and the final recommended path requires human handling

### Requirement: Model failure has a deterministic safe fallback
The system SHALL generate a difference-type-specific Chinese manual path when the enterprise model is unavailable, times out after retries, or repeatedly violates the output contract.

#### Scenario: Gateway is not configured
- **WHEN** a semantic difference requires a model but no enterprise gateway is configured
- **THEN** the result explains in Chinese what evidence to inspect and how to create a manual pending-execution proposal without claiming that AI completed the reasoning

### Requirement: Analysis v3 is immutable and versioned
The system SHALL store `analysis-v3` results as immutable records bound to the current difference version with safe provider, model, prompt, Skill, tool, usage, attempt, and generation provenance.

#### Scenario: Difference changes after analysis
- **WHEN** the current difference version no longer equals the version referenced by an analysis
- **THEN** that analysis remains readable for audit but cannot be adopted into a new governance proposal

#### Scenario: Historical analysis is read
- **WHEN** a user reads `analysis-v1` or `analysis-v2`
- **THEN** the system returns the historical record without rewriting its text or treating it as `analysis-v3`


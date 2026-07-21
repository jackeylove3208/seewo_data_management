## ADDED Requirements

### Requirement: Compute matching quality before differences
The system SHALL compute per-entity-type counts and ratios for accepted, deterministic, AI-recovered, manual-review, conflict, unmatched, and unconsumed-target records before creating formal differences.

#### Scenario: Student rematching completes
- **WHEN** every current student rematching item is terminal
- **THEN** the quality summary reports initial unresolved, recovered, remaining unresolved, and predicted missing/redundant counts across the full task

### Requirement: Block anomalous reconciliation output
The system MUST block formal difference detection when a versioned quality policy fails, including excessive unresolved ratios, zero accepted parents with dependent children, or anomalous predicted create/disable volume.

#### Scenario: Hundreds of students remain unmatched
- **WHEN** at least 10 students exist and more than the configured default 20 percent remain unresolved
- **THEN** the task stops at the matching quality gate with stable code `matching_quality_gate_failed` and creates no formal student differences

#### Scenario: No class is accepted
- **WHEN** students exist but no class mapping is accepted after rematching
- **THEN** the gate blocks student difference detection and identifies unresolved class context as the cause

### Requirement: Gate failures are actionable and retryable
The system SHALL expose Chinese gate reasons, affected entity types, observed values, thresholds, and allowed recovery actions, and SHALL permit reevaluation after mapping confirmation or rematching retry.

#### Scenario: Operator confirms class mappings
- **WHEN** an operator resolves the mappings named by a failed gate and retries the stage
- **THEN** the system recomputes descendant mappings and evaluates a new quality result without duplicating prior audit records

### Requirement: Passing gates bind final mapping versions
The system SHALL bind difference detection to the exact current mapping versions and quality-policy version that passed the gate.

#### Scenario: Mapping changes after gate evaluation
- **WHEN** a current mapping is superseded before difference detection commits
- **THEN** the stale gate result is rejected and quality evaluation runs again

### Requirement: Quality gates prevent premature governance analysis
The workflow SHALL NOT enqueue governance AI analysis while matching quality is blocked or rematching work remains non-terminal.

#### Scenario: Rematching is still running
- **WHEN** some student rematching work items are queued, running, or retrying
- **THEN** the UI displays matching recovery progress and hides formal difference and governance summaries

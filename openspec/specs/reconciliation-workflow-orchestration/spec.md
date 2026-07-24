# Reconciliation workflow orchestration Specification

## Purpose

Define durable, tenant-safe advancement for legacy and new Agent task workflows.

## Requirements

### Requirement: Automatically progress the reconciliation workflow
For `new-agent-v1` tasks, the system SHALL advance through school-lock acquisition, ingestion, identity work, bounded analysis, conflict decisions, grouped approvals, execution, verification, and reporting under the supervisor; historical tasks SHALL remain on their stored legacy workflow.

#### Scenario: New Agent task starts
- **WHEN** a user confirms start and the school lock is available
- **THEN** backend workers advance persisted phases without browser-owned matching/difference commands

#### Scenario: Historical task is reopened
- **WHEN** its workflow version is legacy
- **THEN** existing snapshots, mappings, differences, analyses, reports, and restore records remain readable without migration

### Requirement: Bound each workflow advancement
One worker claim SHALL process at most one deterministic phase unit, one connector page/atomic mutation, or one model batch containing at most 50 work items.

#### Scenario: Many analysis items exist
- **WHEN** more than 50 work items require one Skill
- **THEN** separate leased batches are persisted and completed independently

### Requirement: Persist observable workflow progress
The system SHALL persist supervisor phase, sub-agent/Skill version, status, attempts, completed/total counters, lock ownership, approval/conflict counts, mutation counts, timestamps, and structured errors independently of browser state.

#### Scenario: Process restarts
- **WHEN** snapshots, batches, decisions, or operations were committed before restart
- **THEN** recovery reuses them and does not repeat completed model calls or target writes

### Requirement: Prevent concurrent duplicate advancement
The system SHALL serialize phase transitions for one run, enforce one active run per school, and make work, decision, plan, report, and execution commands idempotent.

#### Scenario: Two workers claim the same batch
- **WHEN** concurrent claims occur
- **THEN** only one valid lease commits the terminal outcome

### Requirement: Expose safe retry behavior
The system SHALL retry a model or retryable connector operation no more than three times after its initial attempt, SHALL preserve completed work, and SHALL not fabricate AI outcomes or automatically release the school lock after exhaustion.

#### Scenario: Model retries exhaust
- **WHEN** all four total attempts fail
- **THEN** the task becomes blocked, emits a sanitized conversation error, and waits for explicit termination

#### Scenario: Data contract cannot be inspected
- **WHEN** ingestion cannot map the source contract
- **THEN** the task records a data-error report and performs no governance operation

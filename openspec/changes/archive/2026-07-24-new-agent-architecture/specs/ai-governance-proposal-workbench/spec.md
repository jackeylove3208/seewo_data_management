## MODIFIED Requirements

### Requirement: Use real reconciliation data in the Web workbench
The Web application SHALL retrieve Agent phases/events, actionable findings, analyses, clarification/approval state, execution progress, reports, and rollback state from typed backend APIs for non-demo tasks.

#### Scenario: User opens a real Agent task
- **WHEN** the task came from conversation, CSV, API, or database sync
- **THEN** no demo differences, local-only history, or browser-invented stage is substituted

### Requirement: Display the four-stage workflow
The task detail SHALL present data access, Agent analysis/decision, governance execution, and reporting/rollback with persisted sub-progress, current controls, errors, and terminal report state.

#### Scenario: Agent analysis is active
- **WHEN** ingestion, identity work, or bounded analysis is incomplete
- **THEN** stable progress identifies current sub-agent and completed/total work without exposing internal prompts

#### Scenario: Reduced motion is preferred
- **WHEN** the operating system requests reduced motion
- **THEN** active phases use static indicators while retaining textual status

### Requirement: Open one difference analysis modal on demand
The workbench SHALL open detail for one actionable Agent finding on demand and SHALL show authoritative/Seewo values, category, evidence, risk, provenance, and every validated solution without opening correct silent records.

#### Scenario: Finding analysis is available
- **WHEN** a user selects one actionable finding
- **THEN** its persisted current version and solution state are displayed without regenerating AI output

### Requirement: Present validated AI options
The workbench SHALL present at most three validated solutions, the recommendation, rationale, preconditions, executability, risk, and grouping/approval state and SHALL not accept client-authored operation payloads.

#### Scenario: Low-risk solution is selected automatically
- **WHEN** server policy classifies it low risk after all analysis completes
- **THEN** the UI displays its planned status and exact preview while execution remains backend-controlled

#### Scenario: High-risk group is waiting
- **WHEN** compatible findings form one frozen approval group
- **THEN** one card presents agree/reject and pageable membership rather than one modal per record

### Requirement: Support manual-only analysis
The workbench SHALL distinguish unresolved identity conflict from model-resolved high risk and SHALL temporarily enable scoped conflict conversation with interpretation confirmation rather than displaying absence of AI output.

#### Scenario: User provides conflict guidance
- **WHEN** the Agent drafts a structured interpretation from allowed candidates
- **THEN** the UI shows confirm or restate and no governance operation exists before confirmation

### Requirement: Persist both proposal sources through one contract
The backend SHALL adapt Agent-selected, server-policy-selected, and human-clarified solutions into immutable versioned governance proposals/plans with backend-owned identity, evidence, approvals, and supersession history.

#### Scenario: Human clarification is confirmed
- **WHEN** a conflict decision receives second confirmation
- **THEN** a proposal references that decision version rather than trusting free-form client changes

### Requirement: Detect stale difference and target values
The Agent approval, proposal, and execution APIs SHALL reject stale finding, group, plan, snapshot, connector version, or before-value evidence and SHALL require recomputation or restatement.

#### Scenario: Target changes before execution
- **WHEN** current value differs from the approved plan expectation
- **THEN** the operation is blocked and reported rather than overwriting newer data

### Requirement: Stop before governance execution
The workbench SHALL never mutate a target directly; it SHALL submit only versioned start, decision, approval, termination, and rollback commands, while the governance sub-agent executes validated plans through backend connectors.

#### Scenario: User agrees to high-risk group
- **WHEN** the approval command succeeds
- **THEN** the card records approval and backend execution may later proceed, but the browser does not send target field changes

### Requirement: Consume typed backend contracts as the sole workflow truth
The frontend milestone SHALL render only typed, versioned backend APIs and persisted event cursors for Agent state, findings, approvals, reports, history, and rollback. It SHALL NOT read Agent persistence directly, generate operations, use localStorage as task-history truth, or infer a lock/phase/mutation state from UI state.

#### Scenario: A browser reconnects after another client advances work
- **WHEN** the workbench resumes with an event cursor
- **THEN** it refreshes from backend facts and renders the current server state without replaying or inventing a client-side transition

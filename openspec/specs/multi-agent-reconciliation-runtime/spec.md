# Multi-agent reconciliation runtime Specification

## Purpose

Define the durable, school-exclusive supervisor lifecycle for new Agent sync and rollback tasks.
## Requirements
### Requirement: Execute a fixed supervisor lifecycle with specialized sub-agents
The system SHALL persist one versioned supervisor run per sync or rollback task, SHALL advance only the server-owned ordered phases, and SHALL allow a specialized sub-agent to plan and call allowed tools only inside its current phase.

#### Scenario: Normal sync completes
- **WHEN** ingestion, analysis, decisions, execution, verification, and reporting finish
- **THEN** the run records every phase outcome in order and becomes terminal only after its report is committed

#### Scenario: Sub-agent requests a later phase
- **WHEN** a sub-agent attempts to execute governance before analysis and approvals are terminal
- **THEN** the backend rejects the transition and preserves the current phase

### Requirement: Enforce one active task for each school
The system SHALL derive `school_id` only from the server-issued `OperatorContext.tenant_id`, SHALL reject any client-supplied tenant override, SHALL acquire one durable tenant/school lock before starting a conversational sync, manual sync, or rollback task, and SHALL reject another start while the lock is owned.

#### Scenario: Second entry point starts work
- **WHEN** one school has an active conversational sync and a user submits external data sync
- **THEN** the second command is rejected with the current lock owner and safe task status

#### Scenario: Backend restarts while locked
- **WHEN** the owning worker or API restarts during an active task
- **THEN** the same run retains lock ownership and resumes without allowing a competing task

### Requirement: Persist conversations separately from sequential tasks
The system SHALL persist long-lived conversations and SHALL allow one conversation to create multiple sequential tasks only after each prior task becomes report-complete or explicitly terminated.

#### Scenario: User confirms a task start
- **WHEN** the Agent has recognized source, target, whole-school scope, and selected entity types and the user activates start
- **THEN** the backend creates one task, acquires the school lock, and binds it to the conversation

#### Scenario: User attempts another instruction during active work
- **WHEN** a task is active and the conversation is not waiting for scoped conflict clarification
- **THEN** the backend refuses a new free-form command and exposes only commands allowed by current state

### Requirement: Persist resumable Agent work and events
The system SHALL persist run phase, work units, leases, heartbeats, attempts, immutable terminal outcomes, progress counters, and ordered conversation events independently of browser state.

#### Scenario: Browser reconnects
- **WHEN** a user reloads after progress, approval, or error events were written
- **THEN** the client resumes from an event cursor and observes every persisted event without recreating work

#### Scenario: Worker lease expires
- **WHEN** a non-terminal work lease expires without a heartbeat
- **THEN** another worker may claim that work using the same idempotency/input hash while completed work remains immutable

### Requirement: Bound model failure and explicit termination
The system SHALL make one initial model call plus at most three retries, SHALL enter a blocked model-error state after the last failure, SHALL retain the school lock, and SHALL release the lock only after explicit termination produces a terminal report.

#### Scenario: Fourth total attempt fails
- **WHEN** the initial model call and three retries fail for one batch
- **THEN** downstream phases stop and the conversation receives a sanitized error event with a terminate command

#### Scenario: User terminates a blocked run
- **WHEN** the user explicitly terminates a blocked task
- **THEN** the supervisor stops new work, records the termination report, releases the school lock, and permits the next task

### Requirement: Preserve versioned cross-milestone handoffs
The system SHALL allow independently delivered milestones to consume only persisted, versioned
server contracts for task, run, Graph definition, ingestion, execution, provider/Adapter, phase,
lease, event, checkpoint, finding, decision, operation, report, and restore state. A resumed run
SHALL use the versions frozen when it was created and SHALL NOT derive a newer Graph or ingestion
contract from current feature flags.

#### Scenario: A later worker uses a stale analysis context
- **WHEN** a governance or reporting worker attempts to persist a result with an expired lease, a
  different phase, a superseded contract version, or a changed evidence manifest
- **THEN** the backend rejects the write without changing prior milestone facts or releasing the
  school lock

#### Scenario: Ingestion v3 is enabled after an older run starts
- **WHEN** a model-mediated-v1 or source-ingestion-v2 run resumes after the API feature is enabled
- **THEN** it continues under its stored Graph, ingestion, and execution versions and never enters
  API role-binding logic

### Requirement: Materialize remote sources in a versioned graph before inspection
Remote-source sync tasks SHALL use `agent-sync-graph-v2` with a deterministic
`materialize_sources` node between school-lock acquisition and source inspection, while existing
tasks SHALL continue restoring with their persisted graph version.

#### Scenario: Remote-source task starts
- **WHEN** a confirmed conversation task contains a valid `remote_csv` authoritative source
- **THEN** the graph enters `materialize_sources` and cannot inspect or normalize authority before materialization succeeds

#### Scenario: Existing graph task resumes
- **WHEN** an `agent-sync-graph-v1` task resumes after deployment
- **THEN** it follows the original node and action vocabulary without a synthetic materialization transition

#### Scenario: Local or uploaded task starts
- **WHEN** a new task has no remote source
- **THEN** it continues to use the existing sync graph behavior and does not execute a remote download action

### Requirement: Select the materializing Graph for API authority tasks
The supervisor SHALL create new API authority sync runs with `agent-sync-graph-v2`,
`source-ingestion-v3`, and `deterministic-execution-v2`, SHALL acquire the school lock before API
capture, and SHALL enter source inspection only after materialization evidence is complete.

#### Scenario: API task starts
- **WHEN** an authenticated user confirms one valid API-authority/database-target task
- **THEN** the run transitions from school-lock acquisition to `materialize_sources` and dispatches
  the task-bound `api-source` resource before inspection

#### Scenario: Another provider connection is added
- **WHEN** the tenant later selects another connection implemented by a registered Adapter
- **THEN** the supervisor uses the same Graph node set and does not publish a per-connection or
  per-provider graph definition

## ADDED Requirements

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

## MODIFIED Requirements

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

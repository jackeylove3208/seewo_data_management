## ADDED Requirements

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

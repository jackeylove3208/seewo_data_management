## ADDED Requirements

### Requirement: Keep remote links out of manual synchronization
The manual-sync page and manual task API SHALL remain limited to their existing configured or
uploaded source contracts and SHALL NOT expose or accept `remote_csv`, a remote URL, or a
`remote_source_id`.

#### Scenario: User opens manual sync
- **WHEN** the manual-sync page is rendered
- **THEN** it shows no remote-link input or remote-source option

#### Scenario: Client forges a manual remote source
- **WHEN** a client calls the manual task endpoint with `remote_csv`, a URL, or a `remote_source_id`
- **THEN** validation rejects the request before a task, run, source binding, or school lock is created

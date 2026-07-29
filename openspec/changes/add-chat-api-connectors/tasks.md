## 1. Versioned ingestion foundation

- [x] 1.1 Add source-ingestion-v3 configuration and freeze it only for newly created API tasks
- [x] 1.2 Introduce immutable authoritative and target role-binding contracts with role-specific checkpoint keys
- [x] 1.3 Replace pair-mode routing with role-binding routing for ingestion-v3 candidates and executors
- [x] 1.4 Add regression tests proving v1/v2 runs resume without entering v3 routing

## 2. API connection control plane

- [x] 2.1 Add API connection, task-bound API authority source, and external identity binding models and migration
- [x] 2.2 Implement provider manifest registry and deterministic Adapter protocol
- [x] 2.3 Implement backend-only secret resolver and safe connection views
- [x] 2.4 Add tenant-scoped connection create/list/read/test/rotate/delete services and API routes
- [x] 2.5 Add capability, visibility, sanitized-error, and cross-tenant security tests

## 3. Graph v2 API materialization

- [x] 3.1 Select agent-sync-graph-v2 for new API-authority tasks
- [x] 3.2 Bind api-source resources during API task creation
- [x] 3.3 Extend materialize_sources candidate generation and action dispatch for api-source resources
- [x] 3.4 Implement atomic paginated API JSONL capture with hashes, counts, versions, and idempotent replay
- [x] 3.5 Add incomplete-pagination, duplicate-ID, retry, tenant-binding, and no-secret materialization tests

## 4. Agent API ingestion

- [x] 4.1 Implement AgentApiIngestionAdapter from frozen provider records to AgentContractRecord
- [x] 4.2 Persist API projections as AgentInputRecord and role-specific normalization checkpoints
- [x] 4.3 Add unavailable-field input marks and exclude unavailable values from ordinary differences
- [x] 4.4 Add replay-conflict, stable-locator/order, fixed-six-field, and no-legacy-record tests
- [x] 4.5 Validate the full api-authority/database-target input contract before identity construction

## 5. Agent identity integration

- [x] 5.1 Implement audited external identity binding repository and management service
- [x] 5.2 Apply valid external bindings before ordinary number/phone/email lookup
- [x] 5.3 Route stale or contradictory bindings to deterministic identity conflicts
- [x] 5.4 Route authority records without ordinary keys or bindings to authority-invalid work
- [x] 5.5 Add binding, no-key, stale-target, conflict, and userid-not-posting tests

## 6. Provider adapters

- [x] 6.1 Implement DingTalk authentication, capability probing, pagination, safe errors, and field projection
- [x] 6.2 Run the shared provider contract suite against a synthetic DingTalk server
- [x] 6.3 Implement WeCom authentication, capability probing, pagination, safe errors, and field projection
- [x] 6.4 Run the same provider contract suite against a synthetic WeCom server without Graph changes

## 7. Conversational configuration

- [ ] 7.1 Extend conversation contracts and intent state with safe API connection selection
- [ ] 7.2 Add secure configuration-session and connection-status cards without secret echo
- [ ] 7.3 Create one idempotent api-authority/database-target task after explicit confirmation
- [ ] 7.4 Add frontend and backend tests for configuration, permission, visibility, retry, and task start

## 8. End-to-end governance and delivery gates

- [ ] 8.1 Verify AgentInputRecord to identity claim, work item, AI batch, finding, risk, and plan flow
- [ ] 8.2 Verify MySQL preflight version drift, idempotent SQL execution, write verification, and reporting
- [ ] 8.3 Add clean PostgreSQL migration smoke coverage for all additive tables
- [ ] 8.4 Run backend pytest, ruff, mypy, frontend tests, lint, typecheck, build, and strict OpenSpec validation
- [ ] 8.5 Document safe provider configuration, local synthetic testing, and rollout/rollback commands

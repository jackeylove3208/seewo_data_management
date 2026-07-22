Implementation is delivered behind default-off feature flags in six milestones: Agent foundation,
CSV analysis-only, complete CSV governance, reports/history/rollback, frontend unification, then
real API/database connectors. A later milestone may depend on completed foundation tasks, but no
unchecked task is implied complete merely because its interface was scaffolded.

## 1. Baseline and migration boundary

- [x] 1.1 Add characterization tests for the current CSV ingestion, matching/difference/analysis workflow, governed execution, reporting, restore, and task-deletion behavior before changing orchestration.
- [x] 1.2 Introduce an immutable workflow-generation/version field and fail-closed rollout flags while keeping the existing task entry on `legacy-v1`; routing the new task API to `new-agent-v1` remains task 10.1.
- [x] 1.3 Document the legacy components that remain read-only compatibility paths and prevent new Agent tasks from invoking vector embeddings, rematching, matching-quality gates, or the legacy `matching -> differences -> analysis` chain.
- [ ] 1.4 Add migration tests proving existing reconciliation tasks, reports, execution records, target versions, and restore records remain readable after all new schema migrations.

## 2. Durable Agent domain and persistence

- [x] 2.1 Add SQLAlchemy models, repositories, and Alembic migrations for conversations, Agent tasks, phase runs, append-only task events, checkpoints, and sanitized failure records.
- [x] 2.2 Add a database-enforced school-wide exclusive lock with owner task, acquisition time, heartbeat/lease metadata, terminal release reason, and uniqueness that survives worker restarts.
- [ ] 2.3 Add persistence for normalized input records, invalid-row marks, exclusion reasons, source locators, immutable source evidence, and connector capability snapshots.
- [ ] 2.4 Add persistence for reconciliation findings, candidate-key evidence, authoritative-row claims, target-row claims, duplicate groups, missing-source findings, and deterministic idempotency keys.
- [ ] 2.5 Add persistence for model batches and attempts, generated Chinese categories, analyses, proposed actions, risk levels, dependency edges, and model provenance.
- [ ] 2.6 Add persistence for grouped approvals, clarification requests, conversation decisions, second confirmations, rejection reasons, and their audit identities/timestamps.
- [ ] 2.7 Add repository-level concurrency, replay, and crash-recovery tests for locks, events, checkpoints, model attempts, approvals, and findings.

## 3. Three-entity ingestion contract and connectors

- [ ] 3.1 Define `agent-contract-v1` schemas for only `department`, `student`, and `teacher`, with class retained solely as an optional student attribute and normalized number/phone/email candidate keys.
- [ ] 3.2 Reuse the existing upload storage, CSV reader, field mapping, normalization, hashing, snapshot, and quarantine modules behind a new ingestion-sub-agent adapter without changing historical snapshot interpretation.
- [ ] 3.3 Implement authority validation: every third-party department/teacher row requires category, name, number, phone, and email; every third-party student row additionally requires class; invalid rows are marked, excluded, never written back, and reported.
- [ ] 3.4 Implement target validation: a Seewo row with no number, phone, or email is marked but retained as a deterministic target-extra candidate; it is never changed during ingestion, while missing category, class, or name remains an ordinary downstream difference.
- [ ] 3.5 Replace placeholder API/database connector behavior with explicit configuration, credential references, paging/streaming, schema discovery, read/write capability declarations, health checks, and actionable configuration errors.
- [ ] 3.6 Keep CSV target versioning as the CSV mutation adapter and add equivalent capability-checked adapters for configured API and database targets; enforce third-party connectors as read-only.
- [ ] 3.7 Add synthetic connector contract tests covering CSV, paged API, database reads, malformed schemas, partial fetch failures, unsupported write capabilities, and secret-free logs.
- [ ] 3.8 Make any unrecognizable input schema end normal processing, route directly to an abnormal-input report, exclude that report from rollback evidence, and retain the task lock until report completion or termination.

## 4. Skill, MCP, and privacy runtime

- [x] 4.1 Define versioned Skills for the supervisor, ingestion, reconciliation analysis, governance execution, reporting, and rollback phases, including allowed inputs, outputs, invariants, stop conditions, and evidence requirements.
- [ ] 4.2 Extend the MCP gateway from the legacy read-only difference scope to phase-scoped tools for connector reads, indexed lookup, proposal persistence, approvals, mutations, verification, reporting, and restore planning.
- [ ] 4.3 Add server-side authorization that binds every MCP call to task, school, phase, connector, actor, and declared capability and rejects arbitrary SQL, filesystem, network, cross-school, and cross-phase access.
- [ ] 4.4 Implement field-level privacy policy with `student.phone` as the only initial high-sensitivity field: tokenized for model input, masked in UI/reports, omitted from logs, and reversible only inside authorized execution adapters.
- [ ] 4.5 Add prompt-injection and untrusted-data boundaries so CSV/API/database text is evidence rather than executable instruction, and require structured schemas for every sub-agent response.
- [ ] 4.6 Add audit and security tests for unauthorized tools, source-write attempts, raw phone leakage, prompt injection, forged task/school identifiers, and sensitive model-error payloads.

## 5. Durable supervisor and workers

- [x] 5.1 Implement the supervisor state machine for start confirmation, lock acquisition, ingestion, analysis, approvals/clarifications, execution, reporting, completed/terminated/failed-waiting states, and rollback tasks.
- [ ] 5.2 Implement dedicated durable workers/handlers for ingestion, reconciliation analysis, governance execution, reporting, and rollback while reusing the existing durable-job polling pattern where appropriate.
- [x] 5.3 Persist every phase transition, lease, event, and checkpoint transactionally so a process restart resumes the incomplete phase without repeating a completed phase unit or releasing the school lock; mutation idempotency remains task 8.4.
- [ ] 5.4 Enforce one active task per school across conversational creation, external-data sync, and rollback; reject or queue no second task until the owner reports completion or is explicitly terminated.
- [ ] 5.5 Implement explicit termination that stops future work, does not auto-rollback committed changes, generates a termination report, and releases the lock only after that report is persisted.
- [x] 5.6 Implement initial model call plus at most three retries per batch; after exhaustion persist a sanitized error event, stop advancement, keep the lock, and advertise termination as the only recovery command; the public command API remains task 10.1.
- [ ] 5.7 Add state-machine tests for duplicate delivery, stale workers, crash/restart, timeout, termination at each phase, report failure, model retry exhaustion, and lock handoff after valid terminal states.

## 6. Reconciliation and mandatory AI analysis

- [ ] 6.1 Build normalized ordinary PostgreSQL indexes for authoritative number, phone, and email lookups, explicitly excluding vector columns, embedding generation, nearest-neighbor retrieval, and matching-quality thresholds from new Agent tasks.
- [ ] 6.2 Reconcile each valid Seewo row against authoritative candidates in student, teacher, then department search order while retaining all evidence and never selecting an ambiguous first hit merely because it was searched first.
- [ ] 6.3 Claim both authoritative and target rows on accepted correspondence; classify a later target claim of the same authority row as a duplicate and propose retaining one target record.
- [ ] 6.4 Classify no authoritative candidate as target-extra with an AI analysis and high-risk deletion proposal; classify every unclaimed valid authority row as target-missing with an AI analysis and creation proposal.
- [ ] 6.5 Route conflicting candidate keys, multiple authoritative candidates, and otherwise undecidable identity evidence into typed human clarification instead of forcing an automated match.
- [ ] 6.6 Partition actionable findings by entity and compatible context into deterministic batches of at most 50, call the configured DeepSeek gateway, and require every finding to receive a Chinese error category, evidence-based analysis, and executable solution.
- [ ] 6.7 Keep correct rows silent in issue output while recording only minimal internal claims/evidence needed for duplicate and missing detection.
- [ ] 6.8 Add deterministic fallbacks that preserve findings and block execution rather than silently omitting mandatory AI analysis or solutions when structured model output is invalid.
- [ ] 6.9 Add focused tests for cross-entity key conflicts, duplicate target rows, unclaimed authority rows, invalid-row exclusion, missing optional fields, 50/51-row batching, correct-row silence, and mandatory AI output completeness.

## 7. Human approvals and conflict dialogue

- [ ] 7.1 Implement risk policy with student-phone exposure/change and destructive deletes initially high risk, plus a versioned server-side extension point for later risk rules.
- [ ] 7.2 Aggregate equivalent high-risk findings by issue type and proposed operation so one agree/reject card can govern a homogeneous batch without merging incompatible evidence.
- [ ] 7.3 Implement conflict cards that temporarily reopen conversation input, present masked structured evidence, accept natural-language operator guidance, and have the model translate it into a bounded typed decision.
- [ ] 7.4 Require a second explicit confirmation of the interpreted conflict decision before it becomes executable, and retain the original text, interpretation, confirmation, actor, and timestamp.
- [ ] 7.5 Treat rejected high-risk groups and unresolved conflicts as non-executable while allowing independent approved work to continue and ensuring reports describe every skipped item.
- [ ] 7.6 Add API and service tests for grouped consent, mixed groups, rejection, clarification parsing, invalid interpretation, second-confirmation denial, privacy masking, and independent continuation.

## 8. Governance planning and execution

- [ ] 8.1 Adapt the existing governance proposal, risk, dependency-graph, preflight, executor, verification, and execution-record services to consume new Agent findings without depending on legacy difference rows.
- [ ] 8.2 Generate only typed allow-listed create/update/delete operations against the Seewo target, bind each operation to source evidence and approvals, and reject any operation that would mutate third-party authority data.
- [ ] 8.3 Resolve dependencies so failed operations block only dependants, independent operations continue, and successful operations are never automatically reverted after a later failure or termination.
- [ ] 8.4 Preserve CSV version artifacts and implement equivalent before/after evidence and verification for API/database mutations, including idempotency and optimistic conflict detection.
- [ ] 8.5 Record verified mutation outcome per operation and batch so deletion policy, reporting, and rollback eligibility are based on actual successful target changes rather than the existence of an execution batch.
- [ ] 8.6 Add end-to-end synthetic tests for create/update/delete, duplicate retention, partial batch failure, dependency blocking, retry idempotency, target conflict, source immutability, and privacy-safe execution evidence.

## 9. Reports, history, deletion, and rollback

- [ ] 9.1 Extend fact-based reporting to cover normal completion, abnormal input, model failure, operator rejection, conflict outcomes, partial execution, termination, and rollback while separating facts from model narrative.
- [ ] 9.2 Ensure invalid authoritative/target rows and excluded findings appear in reports, and mark abnormal-input reports as ineligible evidence for rollback planning.
- [ ] 9.3 Persist backend-owned history records for every task outcome and expose report, phase, risk/approval summary, mutation status, and rollback relationship through typed APIs.
- [ ] 9.4 Change task deletion eligibility to allow deletion only when neither governance nor rollback produced any verified successful target mutation; protect all mutation-bearing task/report/version/restore chains.
- [ ] 9.5 Implement rollback as a newly confirmed school-exclusive Agent task that derives a restore plan only from verified execution facts, waits for human confirmation, uses the governance executor, and produces its own report/history.
- [ ] 9.6 Extend restore conflict and path tests for intervening versions, partial original execution, missing artifacts, rejected rollback approval, rollback termination, and independent rollback audit trails.

## 10. APIs and event contracts

- [ ] 10.1 Add typed APIs for conversations, start-confirmation previews, task submission, active school lock, phase/status reads, event streaming or polling cursors, termination, and sanitized failures.
- [ ] 10.2 Add typed APIs for issue groups, high-risk approvals, conflict dialogue, interpreted-decision confirmation, reports, backend task history, deletion eligibility, rollback preview, and rollback confirmation.
- [ ] 10.3 Return stable machine-readable error codes for lock conflict, invalid state, connector capability failure, approval required, clarification required, retry exhausted, immutable history, and stale version.
- [ ] 10.4 Update OpenAPI schemas and API contract tests, including authorization identity from backend context rather than client-supplied operator IDs and compatibility reads for legacy tasks.

## 11. Frontend migration

- [ ] 11.1 Replace the deterministic local assistant in `ConversationCreatePage` with the backend conversation/event APIs while keeping the existing large chat layout and adding the explicit pre-start confirmation card.
- [ ] 11.2 After submission, disable ordinary conversation input and show only live phase progress plus termination, reopening input solely for typed conflict clarification and closing it after response submission.
- [ ] 11.3 Render grouped high-risk agree/reject cards, masked conflict evidence, natural-language clarification, interpreted-decision second confirmation, model retry errors, and terminal/termination reports in the conversation timeline.
- [ ] 11.4 Route `TaskCreatePage` external-data sync through the same Agent start API, remove reconciliation scope and processing-mode controls, keep department/student/teacher selection, and support CSV/API/database source and target configuration.
- [ ] 11.5 Replace localStorage task history as the source of truth with backend history, show completed/terminated/failed/rollback relationships, and enable delete only from server-provided eligibility.
- [ ] 11.6 Update task detail, execution detail, reporting, and restore screens to display the new phases and findings while preserving legacy rendering for historical workflow versions.
- [ ] 11.7 Add React unit/integration tests and Playwright journeys for both entry points, exclusive-lock blocking, grouped approval, conflict dialogue, termination, abnormal input, partial execution, report history, deletion protection, and confirmed rollback.

## 12. Rollout, observability, and quality gates

- [ ] 12.1 Add structured privacy-safe metrics and logs for phase duration, queue age, lock owner/age, connector failures, model retries, batch sizes, approvals, mutations, report completion, and rollback outcomes.
- [ ] 12.2 Add operator diagnostics for stuck locks and failed phases without exposing row contents or secrets, plus a documented audited recovery procedure that never silently unlocks an active task.
- [ ] 12.3 Add configuration validation for DeepSeek credentials/model, connector capabilities, batch maximum 50, retry count three, privacy policy version, workflow feature flag, and worker availability.
- [ ] 12.4 Run the PostgreSQL clean-migration smoke test and full backend pytest, Ruff, and mypy gates with only synthetic fixtures and no live model credentials.
- [ ] 12.5 Run frontend unit tests, lint, typecheck, production build, and Playwright end-to-end tests against the synthetic Agent backend.
- [ ] 12.6 Update development/runbook documentation for API, durable Agent worker(s), connector configuration, school lock lifecycle, model failure recovery, report completion, termination, and rollback.
- [ ] 12.7 Execute a staged migration rehearsal proving new tasks take the Agent route, legacy tasks remain readable/restorable, vector/rematching workers are not required for new tasks, and rollback creates an independent history record.

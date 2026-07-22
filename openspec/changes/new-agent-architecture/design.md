## Context

The current repository is not a blank slate. It already has a FastAPI/SQLAlchemy/PostgreSQL backend, immutable paired snapshots, canonical entity records, CSV readers and target versioning, an OpenAI-compatible DeepSeek provider, task-scoped tokenization, a Skill registry, a read-only MCP gateway, leased AI jobs, governance proposals, execution plans, operation verification, reports, historical restore plans, and a React workbench. Those foundations are useful, but the present composition is the old architecture:

- `ReconciliationWorkflowService` advances matching, differences, and analysis as separate backend services.
- `EntityResolutionService`, exact/scored matching, rematching, vector support, and matching-quality gates decide whether the task can reach analysis.
- `GovernanceAgent` runs one named Skill and returns one analysis contract; it is not a durable multi-agent runtime.
- `MCPToolGateway` exposes only difference-scoped read-only tools.
- `DatabaseSourceConnector`, `ThirdPartyApiConnector`, and `SeewoApiConnector` are explicit but unconfigured extension points; CSV is the only complete ingestion path and CSV versioning is the only mature mutation target.
- `ConversationCreatePage` uses a frontend-only deterministic assistant and non-persistent React messages.
- `TaskCreatePage` exposes scope and snapshot-mode controls and still submits the legacy workflow.
- recent task history is reconstructed from `localStorage`, while deletion protection currently checks for an execution batch rather than a verified successful mutation.

The new product model keeps third-party data authoritative and makes the backend appear as one intelligent data-sync Agent. Internally, a server-owned supervisor coordinates specialized sub-agents. The main phase order is fixed; a sub-agent may plan only inside its current phase and may call only its phase-specific versioned Skills and MCP tools. This design deliberately avoids a monolithic Agent with arbitrary database or connector permissions.

The agreed business contract is also narrower than the legacy canonical graph. New Agent tasks reconcile only department, student, and teacher. Class is a student field. Number, phone, and email are identity candidate keys; name, category, and class are ordinary governed fields. Historical tasks retain the legacy organization-unit/class/teacher/student/membership representation.

## Goals / Non-Goals

**Goals:**

- Route both conversational sync and manual external sync through one durable supervisor lifecycle.
- Coordinate data-ingestion, reconciliation-analysis, governance-execution, report, and rollback sub-agents with versioned Skills and scoped MCP tools.
- Preserve server ownership of phase order, tenant/school isolation, permissions, state transitions, batch membership, risk policy, approvals, execution, and audit facts.
- Support configured CSV, API, and database connectors through one read/version/apply/verify contract, while reporting unsupported connector capabilities instead of fabricating success.
- Normalize new tasks to department, student, and teacher records and mark/exclude incomplete authoritative rows without modifying the third-party system.
- Use ordinary normalized PostgreSQL indexes over number, phone, and email; do not use embedding models or vector retrieval.
- Analyze bounded work items in batches of at most 50 through the configured DeepSeek gateway.
- Keep correct records silent and generate a Chinese category, evidence-backed AI analysis, risk, and governance solutions for every actionable record.
- Automatically execute low-risk plans only after all analysis is complete; require grouped approval or conflict clarification for high-risk plans.
- Guarantee that student phone is the only high-sensitivity field in this change: tokenized for model/MCP use, masked in ordinary UI/report output, absent from logs, and always high-risk to govern.
- Persist all state so refreshes and process restarts resume the same task without duplicating work.
- Generate a terminal report for every completed, data-error, partial, terminated, and rollback task.
- Protect any task with at least one verified successful target mutation from deletion and use execution/version facts, not report prose, as rollback truth.

**Non-Goals:**

- The supervisor or any model does not modify third-party authoritative data.
- The change does not build or depend on a vector database, local embedding model, embedding API, Top-1/Top-3 semantic search, or the unfinished rematching graph.
- The change does not delete legacy tables or rewrite historical task records.
- A sub-agent does not receive arbitrary SQL, filesystem, URL, credential, or connector access.
- Natural-language user text does not directly become a target mutation; it must resolve to a validated structured decision and, for conflicts, receive a second confirmation.
- Reports do not reconstruct rollback operations. Rollback is derived from verified execution attempts and target versions.
- The frontend does not become workflow truth. It renders persisted conversation/task/events and sends commands.
- This demo does not implement login, school selection, or role administration. The trusted school ID is `OperatorContext.tenant_id`; a future identity provider replaces only that backend context provider and does not change Agent, lock, task, event, or audit models.

## Decisions

### 1. Use a durable supervisor with fixed phases and stage-internal autonomy

One `AgentRun` belongs to one reconciliation or rollback task. The supervisor advances only legal persisted phases:

```text
intent_confirmed
  -> acquire_school_lock
  -> ingest_and_normalize
  -> build_identity_work
  -> analyze_batches
  -> clarify_identity_conflicts
  -> aggregate_risk_and_approvals
  -> compile_execution_plan
  -> execute_and_verify
  -> generate_report
  -> terminal
```

Data-contract failure can transition from ingestion to a data-error report. A user termination transitions from any active phase to cancellation, drains the current atomic operation, generates a termination report, and becomes terminal. A rollback run uses `plan_restore -> clarify_restore_conflicts -> approve_restore -> execute_restore -> report_restore` under the same school lock.

The supervisor is deterministic about phase ordering and invariants. Each sub-agent receives a phase Skill, a bounded evidence manifest, and an allowed-tool set, and may decide which allowed read tool to call or how to explain evidence. It cannot select a different phase, create writes, or release the lock.

Alternative: one general-purpose Agent with all tools. Rejected because it could skip validation/approval, mix task scopes, or repeat writes after a restart. Alternative: retain a purely fixed legacy pipeline. Rejected because it cannot support conversational conflict clarification and unified Agent operation.

### 2. Separate a long-lived conversation from school-exclusive tasks

A conversation is long-lived and may create many sequential tasks. Before task creation, the user can speak freely and the backend Agent maintains private intent context. Once it has a complete source, target, whole-school scope, and selected entity types, it produces an in-message start-confirmation card. Only `start_sync` creates a task and attempts to acquire the school lock.

The lock key is the authenticated tenant/school ID, not the conversation ID. Conversational sync, manual sync, and rollback share the lock. It is represented by a durable row with task/run ownership and an audit trail; it does not silently expire. Process recovery resumes the owning run. Only report-complete or explicit termination releases it. A stale lock requires an authorized termination/recovery command rather than a new competing task.

For this demo, `school_id` is exactly the server-issued `OperatorContext.tenant_id`. Development continues to use the configured demo tenant. No client command may supply, override, or switch that value. A later authentication and multi-school change may replace the `OperatorContext` dependency provider, but must preserve the tenant key already used by Agent runs, locks, tasks, events, and audit facts.

While a task is active, the general composer is disabled. It is temporarily enabled only in an identity-conflict clarification state and is constrained to that frozen conflict batch. High-risk approvals use cards rather than free-form commands. Model failure exposes a sanitized error event and a termination command.

### 3. Preserve legacy entity types while introducing a new three-entity task contract

Changing the global `EntityType` enum in place would break historical snapshots, governance operations, reports, and restore records. New tasks therefore bind an `agent-contract-v1` schema containing:

| Entity | Required authoritative fields | Governed fields |
| --- | --- | --- |
| department | category, name, number, phone, email | name, category, number, phone, email |
| teacher | category, name, number, phone, email | name, category, number, phone, email |
| student | category, name, number, class, phone, email | name, category, number, class, phone, email |

Teacher and department class is structurally not applicable. The new canonical payload can be implemented as a versioned record projection over `CanonicalEntityRecord` rather than removing legacy classes/memberships. New APIs expose only department/student/teacher for Agent tasks. Historical APIs continue decoding legacy values.

If an entire source cannot be inspected or mapped to the six-field contract, the run skips reconciliation/governance and generates a data-source error report. An authoritative row missing any applicable field is marked, retained as immutable ingestion evidence, excluded from identity indexing and reconciliation, and listed in the final report. It never produces a write to the third-party system.

For Seewo, number, phone, and email are candidate identity keys. Missing category, class, or name is a normal governed difference. A target record with no match in student, then teacher, then department becomes target-extra and receives an AI analysis plus a high-risk delete/disable proposal. The input sub-agent only marks evidence; it never deletes a target record.

### 4. Generalize connectors without pretending current stubs are complete

Reuse `SourceConnector`/`TargetConnector`, file storage, CSV inspection, and `ConnectorRegistry`, but add persisted connector definitions and capabilities:

- source role, connector kind, configuration reference, supported entity types;
- read/version/pagination semantics;
- supported mutation operations and reversibility;
- stable source locator and target version token;
- health/readiness diagnostics without secrets.

CSV reads reuse current readers and snapshots. CSV writes reuse target versioning and always create a new file version. API and database connector implementations must be added behind server-side configuration; secrets are referenced by ID and never placed in conversation messages or persisted prompts. Database writes use a configured target adapter with parameterized operations and transaction/version checks, never a model-generated query. API writes use idempotency keys and read-after-write verification.

An unsupported operation remains visible as a non-executable AI solution and report item. The system never reports an API/database connector as implemented merely because a placeholder class exists.

### 5. Build deterministic identity evidence with ordinary PostgreSQL indexes

After ingestion, the backend normalizes non-empty number, phone, and email values and builds task/snapshot/entity-partitioned lookup records. These are ordinary relational rows/indexes, not vectors. A target record queries authoritative student, teacher, and department partitions in that order and persists all evidence:

- one or more supplied keys consistently identify one authoritative record: create a resolved work item;
- one supplied key identifies one record: create a resolved work item and treat other missing fields as differences;
- a key matches multiple records, or different keys identify different records: create an identity-conflict work item;
- no key identifies any authoritative record: create a target-extra work item;
- an indexed authoritative record remains unclaimed after target processing: create a target-missing work item.

Name, category, and class never establish identity. If number, phone, and email all fail to match, the target is target-extra and the unclaimed authority row is target-missing even when name and class are identical. The order student/teacher/department is search order only and cannot override contradictory evidence.

Target rows have a connector-stable order: CSV physical row number, API stable cursor plus record ID, or database configured primary-key ordering. A source without stable replay order is a data error. When several target rows resolve to one authoritative record, the earliest stable target claim is retained and may receive ordinary authoritative field completion; each later claim becomes target-extra/duplicate work. Claims are append-only and unique for the exact snapshot pair so retries and concurrent workers reproduce the same decision.

Marked invalid authoritative rows are excluded from identity indexes and behave as absent during reconciliation. They nevertheless create separate mandatory AI anomaly findings and report facts describing missing fields and likely reconciliation impact. Such findings may recommend repairing the third-party source and rerunning, but cannot become third-party mutations.

Resolved and actionable work items are grouped by entity type and split into immutable model batches of at most 50. This limit applies only after each target row has queried the complete authoritative PostgreSQL index; it never partitions the searchable authority population. A batch stores an input hash, evidence IDs, Skill version, attempt count, lease/heartbeat, output hash, model provenance, and independent item outcomes. Completed items are never regenerated on retry.

Alternative: randomly send 50 source rows and 50 target rows to the model. Rejected because corresponding records can cross batch boundaries and the model cannot guarantee complete coverage. Alternative: vector retrieval. Rejected by product decision and unnecessary with defined identity keys.

### 6. Make Skills contracts and MCP authorization the primary hallucination boundary

The minimum versioned Skill set is:

- `orchestrate-school-data-sync` for supervisor phase invariants;
- `inspect-external-data-source` and `normalize-organization-data-batch` for ingestion;
- `reconcile-entity-batch` and `generate-governance-solutions` for analysis;
- `aggregate-risk-approvals` for frozen grouped decisions;
- `resolve-human-conflict-instruction` for bounded clarification interpretation;
- `execute-approved-governance-plan` for execution coordination;
- `generate-governance-report` for fact-grounded reports;
- `assess-rollback-impact` and `execute-approved-rollback` for restore work.

Existing report/rollback/analysis Skills can be evolved, but their versions and output schemas must be explicit. The current `SkillRegistry` read-only global allowlist becomes phase-aware; write-capable tools are never enabled merely because a Skill names them.

MCP tools are split into capability sets:

- ingestion: inspect configured source, read bounded page, persist canonical batch/mark;
- analysis: read work item, query normalized identity evidence, read authoritative/target fields, persist validated outcome;
- approval: read frozen group, persist authenticated decision, interpret only listed candidates/actions;
- execution: preview plan, read approval, execute already-persisted operation, verify result;
- report/restore: read immutable execution facts/versions, build restore preview, execute an approved restore batch.

Every call receives a server-issued context containing tenant, conversation, task, run, phase, snapshot pair, plan version, approval ID where applicable, and allowed resource IDs. There is no generic execute-SQL, read-file-path, fetch-URL, or connector-credential tool. Candidate IDs, fields, operations, batch cardinality, tokens, and versions are validated after every model response.

### 7. Treat only student phone as high-sensitivity in the new contract

The new privacy policy classifies `student.phone` as the sole high-sensitivity field for this change. Before any external model call or MCP model-facing payload, its raw value becomes a deterministic task-scoped token. Ordinary frontend/report views show a masked value. Logs, error events, traces, prompt persistence, and provenance omit the raw value.

The backend retains the raw value in authorized immutable snapshots and loads it only when a validated approved operation executes. Unknown model tokens fail validation. Skills repeat the prohibition, but code-level policy and MCP serialization enforce it.

Legacy tasks keep their existing broader tokenization behavior. The new policy does not retroactively expose previously tokenized historical payloads.

### 8. Separate model-resolved high risk from evidence conflicts

Risk is server-owned; model labels cannot reduce it. Low-risk automatic work is limited to uniquely resolved, reversible normalization or filling absent non-sensitive values from authoritative evidence. High risk includes student-phone governance, create/delete/disable/merge, replacement of existing identity/category/class values, ambiguous identity, destructive bulk work, and every rollback.

All analysis completes before any target write. High-risk items are grouped by stable finding kind, entity type, operation type, risk-policy version, and compatible preconditions. Each approval freezes exact item/version IDs and a content hash. A model-resolved group displays one accept/reject card even if it contains 50 or more records; details can be paged without changing membership.

Identity conflicts use a different flow. The conversation displays normalized/masked conflicting evidence and temporarily enables the composer. `resolve-human-conflict-instruction` may select only listed candidates or listed outcomes such as target-extra or skip. It produces a structured draft, the UI echoes the interpretation, and the user must choose confirm or restate. Only the confirmed structured decision can feed governance planning.

### 9. Reuse governed execution, but key retries and retention to verified mutations

Reuse `GovernancePlanBuilder`, preflight/version checks, dependency ordering, `ExecutionExecutor`, audit attempts, target versions, and verifier. Adapt proposal inputs from legacy `DifferenceRecord`/analysis where compatibility is practical, or add Agent finding adapters with the same execution contract. The model never sends an operation directly to a connector.

Execution semantics are:

- low-risk operations and approved high-risk operations enter one versioned plan after all decisions finish;
- retryable connector calls receive at most three retries and stable idempotency keys;
- a failed operation blocks dependent operations;
- independent operations continue;
- verified successes remain successful and are not automatically rolled back;
- user termination stops new work and drains/aborts the current connector atomic unit without reversing prior successes;
- partial outcomes are reported precisely.

Deletion eligibility changes from “an execution batch exists” to “a verified successful target mutation exists.” Data-error, rejected-only, model-error-terminated, user-terminated-before-write, and all-failed/no-write tasks may be deleted. Any task with one successful governance or rollback mutation is retention-protected together with its execution facts, reports, target versions, and restore links.

### 10. Generate reports from facts and make rollback a new task

Report generation starts from persisted ingestion, Agent, approval, plan, execution, and version facts. The report sub-agent writes narrative only within those facts. Terminal report kinds include normal, partial success, data error, model-error termination, user termination, and rollback. Reports state skipped/invalid source rows, target-extra/missing/conflict counts, approvals/rejections, successful/failed/blocked operations, and rollback eligibility.

Model report failure follows the same initial call plus three retries. If the user terminates, the backend renders a deterministic terminal summary from facts rather than claiming an AI narrative was produced.

Every rollback request creates a new school-exclusive task and lock owner. The rollback sub-agent reads verified execution attempts and target versions, builds inverse/compensating operations, detects intervening changes, and requests high-risk approval. Conflict clarification uses the same bounded human-input pattern. The execution sub-agent applies the approved restore plan and the report sub-agent creates a distinct rollback report/history record. An ingestion/report narrative with no execution facts cannot be a restore source.

### 11. Replace browser-local orchestration with typed APIs and persisted events

Add typed APIs for conversations/messages, intent/start confirmation, active-school lock state, task/run state, events/progress, terminate, conflict batches and clarification drafts, approval groups, reports, backend history, deletion eligibility, and rollback task creation. Commands require expected versions/idempotency keys. Reads enforce tenant context.

The preferred live transport is persisted event polling or SSE backed by event IDs; reconnect uses `Last-Event-ID`/cursor and cannot lose an approval/error. Browser state caches data but never invents task stages.

### 12. Evolve the existing frontend instead of replacing its visual foundation

Reuse the current full-height conversation surface, left workspace, upload controls, task details, analysis/proposal components, execution/report views, and responsive styles. Replace the deterministic task assistant with backend conversation APIs and render typed message blocks: text, start confirmation, progress, high-risk approval, conflict clarification, sanitized error, terminal report, and rollback confirmation.

The conversation persists across multiple sequential tasks. Its composer is disabled after task start except during scoped conflict clarification. Completed or terminated report events restore free input.

In external data sync, remove reconciliation scope and processing mode, force whole-school/full behavior, and keep task name, configured data-source selection/uploads, and department/student/teacher checkboxes. Both entry points submit the same backend start command.

Replace `localStorage` history with paged backend history. Display distinct normal/partial/data-error/terminated/rollback states. Show delete only when `deletion_eligible` is true; backend remains authoritative.

### 13. Use bounded, durable failure handling

Each model call has one initial attempt plus at most three retries. Invalid JSON, wrong cardinality, duplicate/omitted item IDs, unknown candidates/tokens, gateway timeouts, and provider failures all use the same persisted attempt mechanism. After retries are exhausted, the run enters `blocked_model_error`, holds the school lock, stops downstream work, and posts a sanitized conversation event containing phase, batch, completed count, stable error code, gateway request ID where safe, and attempt count. It never includes a stack trace, credential, raw prompt, connector configuration, or student phone.

The user may terminate the blocked task. There is no automatic lock release or fabricated AI outcome. Process restart recovers leases and the same terminal/blocked state.

## Risks / Trade-offs

- [One school-wide lock can block all work after a failure] → persist clear owner/status/events, recover the same run after restart, provide explicit termination, and never rely on an expiring browser lock.
- [Direct DeepSeek reconciliation can hallucinate identity] → construct deterministic key evidence first, restrict every batch to persisted work items, validate exact membership, and require human clarification for any conflict.
- [Phone is not always truly unique in real school data] → treat keys as candidate evidence rather than an unquestioned database constraint; duplicate hits become conflict work instead of automatic assignment.
- [Strict authoritative completeness can exclude many rows] → retain every excluded row as immutable marked evidence, count it in reports, and never infer target writes from excluded authoritative data.
- [Removing class as an entity can break legacy code assumptions] → version the new contract and API projection; keep legacy enum/table decoding and bypass old matching/difference services only for new Agent tasks.
- [API/database connectors are currently placeholders] → implement capability discovery and fail/report unsupported configurations; do not mark corresponding tasks complete until real connector integration tests pass.
- [Natural-language clarification can be misunderstood] → constrain it to a frozen candidate/action set, validate structured output, echo the interpretation, and require a second confirmation.
- [Grouped approval can hide record-level differences] → freeze and hash membership, expose pageable details, group only compatible operations, and invalidate approval when any member/version changes.
- [Partial execution complicates reports and rollback] → retain per-operation attempts, dependencies, actual-after verification, target versions, and build restore operations only from verified successes.
- [A narrative report could misstate facts] → render counts and operation tables from immutable backend facts and allow the model to produce only bounded narrative fields.
- [Legacy and new workflows coexist] → bind workflow version at task creation, keep route/adapters explicit, and add regression tests for historical task reads and deletions.

## Migration Plan

Delivery uses one OpenSpec change with guarded milestones rather than a big-bang replacement. `NEW_AGENT_ENABLED` defaults off; `NEW_AGENT_ANALYSIS_ONLY` defaults on; CSV execution and API/database connectors have separate default-off switches. Child execution switches fail configuration when their parent runtime/safety mode is not enabled. A task's immutable workflow version never changes when a deployment flag changes.

1. Add the Agent foundation: workflow version, guarded flags, conversation/run/event/checkpoint/failure persistence, school lock, state machine, leased worker harness, versioned Skills, and phase-scoped MCP authorization. Do not expose a new task entry yet.
2. Add CSV analysis-only mode: `agent-contract-v1`, marked records, stable source ordering, ordinary identity indexes, deterministic findings, and mandatory AI solutions while connector writes remain disabled.
3. Enable complete CSV governance: risk, grouped approval, conflict dialogue, second confirmation, governed execution, and verification.
4. Add fact reports, backend history, deletion retention, termination reports, and independent rollback tasks; only then is the CSV lifecycle complete.
5. Connect both frontend entries to the same Agent APIs while retaining legacy task/detail rendering.
6. Add real API and database connector capabilities after the CSV lifecycle passes its quality gates; placeholders remain unavailable.
7. Run clean PostgreSQL migration, backend unit/integration/E2E, connector contract tests, frontend unit/type/lint/build/Playwright, failure/restart/concurrency tests, and a synthetic full lifecycle including rollback.

Rollback of the deployment disables creation/claiming of `new-agent-v1` runs and restores legacy task creation. Existing Agent snapshots, events, approvals, execution facts, reports, and locks remain readable; operators must drain or explicitly terminate active Agent runs before dropping any new tables in a later release.

## Open Questions

No unresolved product decisions remain. Concrete API paths, table names, event payload schemas, configurable bulk-risk thresholds, and connector-specific credential providers are implementation details to finalize under the requirements and tasks in this change.

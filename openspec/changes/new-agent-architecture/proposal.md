## Why

The repository already implements immutable ingestion snapshots, a DeepSeek-compatible model gateway, difference analysis, governed execution, reporting, rollback, and a partially built conversational workbench, but those capabilities are connected by a legacy fixed pipeline whose entity mapping and quality gates can stop valid tasks before AI analysis. The product now requires one durable school-scoped supervisor Agent that coordinates specialized sub-agents across the complete backend lifecycle while retaining deterministic permissions, approvals, audit evidence, and rollback safety.

## What Changes

- **BREAKING** Replace the new-task backend path `entity resolution -> difference detection -> mandatory analysis` with a supervisor Agent that advances a fixed lifecycle and delegates stage-internal planning to data-ingestion, reconciliation-analysis, governance-execution, report, and rollback sub-agents.
- **BREAKING** Remove local embedding, pgvector candidate retrieval, Top-1/Top-3 vector adjudication, rematching, and blocking matching-quality gates from the new-task path. Existing historical records and legacy tables remain readable until a later retirement change.
- Reduce the new Agent workflow to three business entity types: department, student, and teacher. Treat class as a student field rather than a separately reconciled entity, while preserving old entity shapes for historical tasks.
- Add one school-wide exclusive active-task lock shared by conversational sync, manual external sync, and rollback. The lock survives retries, model failures, approvals, page refreshes, and service restarts, and is released only after report completion or explicit termination.
- Add versioned Skills and stage-scoped MCP tools for every sub-agent. Models receive only tenant/task/stage-scoped evidence, cannot execute arbitrary SQL/files/URLs, cannot mutate third-party authoritative data, and cannot bypass server-owned plans or approvals.
- Extend data ingestion to inspect CSV, configured API, and configured database sources, map them to the six-field business contract, mark and isolate invalid records without modifying either system, and produce data-error reports when required.
- Build ordinary PostgreSQL indexes over normalized number, phone, and email values instead of a vector database. Construct bounded reconciliation work items, grouped by student, teacher, and department, and send at most 50 work items to one DeepSeek analysis call.
- Require the analysis sub-agent to produce a Chinese issue category, evidence-backed analysis, risk, and governance solutions for every actionable finding. Correct records remain silent.
- Add two human-in-the-loop interactions in the existing conversation: grouped accept/reject cards for model-resolved high-risk work and a temporary conflict-clarification mode for identity evidence the model cannot resolve. Natural-language clarification is converted to a bounded structured decision and requires a second user confirmation.
- Apply low-risk work automatically only after all analysis is complete. Group high-risk work by stable server-owned type, freeze the exact member/version set, and execute only after persisted approval.
- Keep successful independent governance operations running when another operation fails, block dependent operations, retry retryable calls at most three times, never automatically roll back successful writes, and generate a partial-result report.
- Generate normal, partial, data-error, model-error termination, user-termination, and rollback reports from immutable facts. Treat every rollback as a new school-exclusive high-risk task requiring approval.
- Replace browser-local task history with backend history. Any task with no successful target mutation may be deleted; once any governance or rollback operation changes target data, the task, report, execution evidence, and restore chain are retention-protected.
- Update the external data sync UI to remove reconciliation scope and processing mode, fix scope to the whole school, retain department/student/teacher selectors, and route both frontend entry points through the same Agent lifecycle.

## Capabilities

### New Capabilities

- `multi-agent-reconciliation-runtime`: Durable supervisor/sub-agent execution, fixed phase ordering, school-exclusive locking, retries, cancellation, recovery, and task events.
- `agent-data-ingestion`: Agent-assisted CSV/API/database inspection, three-entity normalization, invalid-record isolation, source authority enforcement, and ingestion reporting.
- `agent-reconciliation-analysis`: Ordinary unique-key indexing, bounded work-item construction, DeepSeek batch analysis, identity-conflict collection, findings, categories, and governance solutions.
- `agent-skill-mcp-security`: Versioned stage Skills, scoped MCP capabilities, structured output validation, student-phone privacy, provenance, and model/tool authorization.
- `agent-governance-execution`: Server-owned risk policy, grouped approvals, clarification decisions, governed connector writes, verification, partial failure, and idempotent retries.
- `agent-reporting-and-rollback`: Fact-grounded terminal reports, protected history, separate approved rollback tasks, restore conflicts, and retention/deletion policy.

### Modified Capabilities

- `conversational-task-creation`: Evolve the frontend-only intent assistant into a persistent multi-task conversation with start confirmation, live task events, approval cards, temporary clarification input, and input locking while a task is active.
- `external-data-sync`: Remove scope and processing-mode controls, retain entity/source selection, and submit manual input into the same school-scoped Agent workflow.
- `reconciliation-left-workspace`: Load history and deletion eligibility from the backend, expose terminal Agent/report states, and preserve all mutation-bearing tasks for rollback.
- `reconciliation-workflow-orchestration`: Replace legacy matching/difference/analysis progression for new tasks with the durable supervisor lifecycle while preserving historical task reads.
- `enterprise-model-gateway`: Support versioned sub-agent Skills, batches of at most 50, exactly three retries after the initial call, strict structured outputs, sanitized conversation errors, and field-scoped student-phone tokenization.
- `ai-governance-proposal-workbench`: Replace per-difference manual workflow surfaces with grouped Agent findings, high-risk approval cards, conflict clarification, execution progress, and report-first completion.

## Impact

- Backend domains affected: `app/ingestion`, `app/connectors`, `app/ai`, `app/workflow`, `app/governance`, `app/executions`, `app/reports`, `app/restores`, task deletion, API routes, schemas, repositories, SQLAlchemy models, and Alembic migrations.
- Frontend domains affected: conversation creation, manual external sync, task workflow/detail, approval and clarification cards, task history, deletion, reporting, rollback, typed API clients, polling/SSE, and E2E coverage.
- Reused foundations: immutable snapshots and source files, PostgreSQL, tenant/operator context, DeepSeek/OpenAI-compatible provider, task tokenization primitives, job leasing patterns, governance plans, operation verification, CSV target versioning, execution audit records, reports, and restore plans.
- Replaced or bypassed for new tasks: `EntityResolutionService`, scoring/exact matcher orchestration, `DifferenceDetectionService` as the workflow gate, matching-quality blocking, vector/rematching workers, browser-only deterministic task assistant, and localStorage as task-history truth.
- New dependencies are limited to application code and database migrations; no embedding model, vector service, or embedding API is required.
- Existing uncommitted archive/spec work and historical tasks are not migrated or deleted by this change.

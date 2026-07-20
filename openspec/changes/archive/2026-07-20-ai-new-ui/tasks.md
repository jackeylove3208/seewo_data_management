## 1. Baseline and domain contracts

- [x] 1.1 Run and record the current backend and frontend test, lint, type-check, and build baselines before changing contracts
- [x] 1.2 Add Pydantic schemas for workflow stage state, bounded advancement responses, progress counters, retry metadata, and stable workflow errors
- [x] 1.3 Add `analysis-v2` Pydantic schemas for zero-to-three governance options, field changes, evidence references, preconditions, recommendation, manual-only reason, and provenance
- [x] 1.4 Add governance proposal schemas for AI selection, manual edits, previews, immutable versions, proposal source, supersession, and `pending_execution` status
- [x] 1.5 Add schema tests that reject multiple recommended options, manual-only output with options, unknown actions, invalid confidence, blank rationale, and protected manual fields

Baseline on 2026-07-17: backend `pytest` passed 215 tests, Ruff check passed, mypy passed, and pip check passed; Ruff format check retained the two pre-existing unformatted files `app/matching/conflict_resolver.py` and `tests/integration/api/test_differences.py`. Frontend Vitest passed 19 tests, ESLint passed, type-check passed, and the production build passed.

## 2. Tenant ownership and task API correction

- [x] 2.1 Add failing API tests proving task creation derives tenant from `OperatorContext` and ignores no client-controlled identity
- [x] 2.2 Remove `tenant_id` from `CreateReconciliationTaskRequest`, inject operator context into create/get routes, and build snapshot scope with the backend tenant
- [x] 2.3 Add tenant guards to task retrieval and workflow operations so cross-tenant access consistently returns 404
- [x] 2.4 Update task idempotency hashing and tests for the new request contract without weakening existing duplicate-request behavior
- [x] 2.5 Remove the hard-coded `demo-school` tenant from frontend task creation and update its request contract tests

## 3. Persistent workflow orchestration

- [x] 3.1 Add SQLAlchemy stage-run persistence with task, stage, attempt, status, progress counts, timestamps, structured error, and retryable flag
- [x] 3.2 Create and test an Alembic migration for stage runs and any task progress fields against upgrade and downgrade paths
- [x] 3.3 Implement a focused stage-run repository with append-only attempt history and current progress aggregation tests
- [x] 3.4 Add failing service tests for snapshots-to-matching, matching-to-differences, differences-to-analysis, completed-stage reuse, restart resume, and invalid stage transitions
- [x] 3.5 Implement `ReconciliationWorkflowService.advance` so one call runs one deterministic stage or one configured analysis batch
- [x] 3.6 Add row locking and idempotency tests proving concurrent advancement cannot duplicate mappings, differences, or analysis records
- [x] 3.7 Add `POST /api/reconciliation-tasks/{task_id}/workflow/advance` and extend task detail responses with persisted workflow state and progress
- [x] 3.8 Add retry API behavior and tests for retryable gateway failures, non-retryable snapshot failures, and preservation of completed work

## 4. Enterprise model gateway configuration

- [x] 4.1 Extend `Settings` with typed authentication header/scheme, response mode, JSON extra headers, JSON extra body, tokenization secret, and analysis batch size
- [x] 4.2 Add configuration validators that enforce object size/type limits and reject reserved header and body overrides such as `model`, `messages`, and `response_format`
- [x] 4.3 Add provider contract tests for `json_schema`, `json_object`, and `prompt_json` requests using `httpx.MockTransport`
- [x] 4.4 Extend `HttpLLMProvider` to merge validated enterprise headers and body parameters, emit the selected response format, and parse compatible structured responses
- [x] 4.5 Add tests for authentication formats, gateway request IDs, timeout/retry behavior, 4xx/5xx classification, invalid JSON, and secret-free error messages
- [x] 4.6 Update readiness output to expose only provider configuration state and add tests proving credentials and configuration values are never returned
- [x] 4.7 Document every real gateway variable in `backend/.env.example` and backend developer documentation, explicitly identifying ignored `backend/.env` as the file for real values
- [x] 4.8 Add an opt-in real gateway smoke test that is skipped unless its explicit environment flag and all required credentials are present

## 5. Task-scoped tokenization boundary

- [x] 5.1 Define protected field categories and tests for person names, phone numbers, emails, and authoritative/target external identifiers
- [x] 5.2 Implement HMAC-based `TaskTokenizationContext` with stable typed tokens, per-call reverse lookup, unknown-token rejection, and no persistent reverse map
- [x] 5.3 Add recursive payload tokenization tests for nested difference evidence, related entities, candidate results, nulls, repeated values, and organization labels that remain available for hierarchy reasoning
- [x] 5.4 Tokenize the Agent's initial input payload before prompt construction and detokenize only validated output references
- [x] 5.5 Wrap every MCP tool result with the same tokenization context before appending it to model messages
- [x] 5.6 Add leakage tests that capture provider requests, tool messages, logs, exceptions, and persisted provenance and prove original protected values are absent
- [x] 5.7 Reject invented tokens and unsupported phone/email after-values and route exhausted invalid responses to manual-only analysis

## 6. Multi-option mandatory analysis

- [x] 6.1 Update the analysis Skill and prompt schema to request `analysis-v2` cause, evidence, manual-only metadata, and zero-to-three structured options
- [x] 6.2 Extend policy validation for allowed operation per difference type, target snapshot membership, field whitelist, before-value match, authoritative after-value, evidence references, risk, and confidence
- [x] 6.3 Add policy tests for safe multiple options, exactly one recommendation, insufficient evidence, uncertain identity/parent, destructive impact, and forbidden operations
- [x] 6.4 Update `GovernanceAgent` to parse and validate `analysis-v2` across tool-call loops while preserving safe provider and MCP provenance
- [x] 6.5 Update deterministic analysis to emit policy-compliant v2 options for clear missing/redundant cases without falsely claiming a model call
- [x] 6.6 Extend analysis persistence and migration so v1 remains read-only and v2 records are immutable and bound to a difference version
- [x] 6.7 Refactor `AnalysisService` to process a bounded batch, persist progress after each item, reuse completed v2 results, and classify manual-only versus failed outcomes
- [x] 6.8 Update analysis list/detail APIs and repository joins to expose v2 options, manual-only reasons, safe provenance, and execution eligibility
- [x] 6.9 Add integration tests using the HTTP fake gateway for real provider wiring, model tool calls, multi-option output, retry exhaustion, and manual-only fallback

## 7. Pending governance proposal backend

- [x] 7.1 Add the immutable `governance_proposals` model with task/difference versions, source, operation, changes, evidence, risk, backend operator, status, timestamps, and supersession link
- [x] 7.2 Create and test the Alembic migration and proposal repository, including stable version allocation and current-proposal lookup
- [x] 7.3 Define backend-owned editable-field policies for organization units, classes, teachers, students, and memberships and expose a read-only editor schema endpoint
- [x] 7.4 Add failing service tests for adopting a persisted AI option without accepting client-rewritten content
- [x] 7.5 Implement AI proposal preview and confirmation from analysis ID, option ID, and expected difference version
- [x] 7.6 Add failing service tests for manual allowed fields, protected fields, blank reason, no-op changes, before-value drift, cross-tenant target, and high-risk content
- [x] 7.7 Implement manual proposal preview and confirmation with server-derived before values, field policy validation, operator provenance, and no target mutation
- [x] 7.8 Implement proposal supersession so revisions create new immutable versions and retain the earlier audit chain
- [x] 7.9 Add proposal create/list/detail API routes and integration tests for AI, operator, conflict, tenant isolation, and unchanged target snapshot/hash

## 8. Typed frontend workflow integration

- [x] 8.1 Add TypeScript domain types and API clients for task workflow state, advancement, retry, paginated differences, analysis-v2, editor schemas, previews, and proposals
- [x] 8.2 Add query keys and TanStack Query hooks with cancellation, bounded polling, page-refresh resume, conflict invalidation, and user-facing API errors
- [x] 8.3 Update task creation to navigate into the persisted workflow and automatically request advancement until analysis-ready or terminal failure
- [x] 8.4 Replace browser-only stage calculations and real-task `demoDifferences` reads while retaining an explicitly isolated demo mode
- [x] 8.5 Add frontend service/hook tests for automatic stage order, no duplicate concurrent advance, retry rules, polling stop conditions, and tenant-free request bodies

## 9. Task and difference workbench UI

- [x] 9.1 Rebuild task detail stage data from backend progress and show ingestion, resolution, differences, and AI statuses with stable dimensions
- [x] 9.2 Add the AI analysis animation, progress bar, completed/manual-only/failed counts, retry feedback, and a reduced-motion static state
- [x] 9.3 Replace the demo difference list with backend cursor pagination, entity/difference/analysis/risk filters, loading skeletons, empty state, error state, and retry
- [x] 9.4 Display real authoritative and Seewo values, changed fields, organization context, match evidence, analysis state, and pending-proposal status per difference
- [x] 9.5 Add responsive desktop and mobile layout rules that prevent long names, values, status tags, filters, and modal actions from overlapping

## 10. AI solution and manual edit experience

- [x] 10.1 Build an on-demand difference analysis modal with source/target context and in-place analysis animation while the selected item is pending
- [x] 10.2 Render cause, evidence, risk, confidence, safe model/rule provenance, one-to-three validated options, recommended state, rationale, and preconditions
- [x] 10.3 Implement AI option before/after preview and confirmation, stale-version conflict handling, and success state marked pending governance execution
- [x] 10.4 Implement manual-only presentation that removes AI adoption actions and explains information gaps or high risk
- [x] 10.5 Build a schema-driven manual editor with appropriate text/select/relation controls, protected read-only context, field validation, and required rationale
- [x] 10.6 Implement manual before/after preview, explicit confirmation, stale-value reload, immutable revision creation, and pending-execution success state
- [x] 10.7 Add Testing Library coverage for pending animation, multiple options, manual-only, protected fields, no-op edits, validation, previews, conflicts, and proposal replacement

## 11. End-to-end verification and handoff

- [x] 11.1 Add a Playwright flow using synthetic CSVs and a local fake enterprise gateway from upload through automatic analysis and AI proposal creation
- [x] 11.2 Add a second Playwright flow for manual-only analysis through whitelisted manual proposal creation and verify no target CSV or snapshot hash changes
- [x] 11.3 Capture and inspect desktop and mobile screenshots for the stage animation, difference list, AI options modal, and manual editor with no blank states or overlap
- [x] 11.4 Run backend pytest, Ruff check/format check, mypy, frontend Vitest, ESLint, TypeScript build, production build, and Playwright suites
- [x] 11.5 Run `openspec validate ai-new-ui`, update `AGENTS.md` only if commands or structure changed, and document the exact local startup and real-gateway smoke-test commands

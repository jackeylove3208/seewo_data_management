## 1. Repository and Local Development Foundation

- [x] 1.1 Create the `backend/`, `frontend/`, `infra/`, and `docs/sample-data/` directory skeleton from `design.md` without adding unneeded feature files
- [x] 1.2 Initialize the Python 3.12 backend package with FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, Polars, HTTPX, Tenacity, pytest, Ruff, and type-checking configuration
- [x] 1.3 Initialize the React and TypeScript frontend with Vite, React Router, TanStack Query, Ant Design, Vitest, Testing Library, and Playwright
- [x] 1.4 Add environment-based backend and frontend configuration with checked-in example files and no committed secrets
- [x] 1.5 Add Docker Compose services for PostgreSQL with pgvector and document the local startup commands
- [x] 1.6 Add backend health and readiness endpoints and a frontend application shell that displays connection failure states
- [ ] 1.7 Add formatting, linting, type-checking, and baseline test commands to repository documentation and CI

## 2. Domain Models and Persistence

- [x] 2.1 Define Pydantic canonical models for organization units, classes, teachers, students, and memberships with source and snapshot provenance
- [ ] 2.2 Define Pydantic models for matches, differences, AI analyses, governance plans, execution operations, reports, and rollback preflight results
- [ ] 2.3 Implement SQLAlchemy models for reconciliation tasks, files, snapshots, canonical entities, mappings, differences, analyses, executions, reports, and rollback links
- [ ] 2.4 Create and apply the initial Alembic migration against a clean PostgreSQL database
- [ ] 2.5 Implement focused repositories for tasks, snapshots, mappings, differences, executions, and reports
- [ ] 2.6 Implement a backend-owned demo operator identity dependency so audit records never accept a client-supplied operator ID
- [ ] 2.7 Add model and repository tests covering constraints, JSON payloads, immutable records, and transaction rollback

## 3. CSV Connectors, Uploads, and Snapshots

- [x] 3.1 Define `SourceConnector` and `TargetConnector` protocols plus connector registry and contract tests
- [x] 3.2 Implement secure upload storage with file size limits, generated storage names, hashes, and original filename metadata
- [x] 3.3 Implement CSV encoding detection, header parsing, empty-file checks, and stable source row numbering
- [x] 3.4 Implement configurable field mappings from third-party and Seewo columns to canonical fields
- [x] 3.5 Implement Polars-based batch validation and quarantine output for invalid or unmapped rows
- [x] 3.6 Implement third-party CSV source and Seewo CSV target connectors that emit canonical input records
- [x] 3.7 Implement immutable raw and canonical snapshot creation with file, mapping, and schema versions
- [x] 3.8 Add paired-upload and task-creation REST endpoints with Pydantic validation and idempotency keys
- [x] 3.9 Add ingestion tests for valid files, missing files, unsupported encodings, malformed rows, missing mappings, and recoverable warnings

## 4. Normalization and Deterministic Entity Matching

- [x] 4.1 Implement pure normalization functions for Unicode, whitespace, phone numbers, email addresses, identifiers, null values, and status enums
- [x] 4.2 Implement configurable organization-path, grade, school-year, class-number, and teacher-display-name normalization
- [x] 4.3 Implement the normalization pipeline with rule-version provenance and unit tests for every rule
- [x] 4.4 Implement historical mapping lookup and revocation behavior
- [x] 4.5 Implement exact matching by entity-specific stable identifiers and composite keys
- [x] 4.6 Implement dependency-ordered matching for organization units, classes, teachers, students, and memberships
- [x] 4.7 Persist match method, evidence, confidence, and confirmation provenance
- [x] 4.8 Add synthetic fixtures and tests for exact matches, missing identifiers, reused manual mappings, and hierarchy-based evidence

## 5. Candidate Retrieval, Scoring, and Difference Detection

- [x] 5.1 Implement blocking partitions by tenant, entity type, campus, grade, and matched parent context
- [x] 5.2 Implement lexical candidate retrieval over normalized names and organization paths
- [x] 5.3 Implement an embedding provider interface and pgvector storage for target entity representations
- [x] 5.4 Implement configurable top-K vector retrieval restricted to compatible blocks
- [x] 5.5 Implement entity-specific weighted scoring with score evidence and first-to-second candidate margin checks
- [x] 5.6 Implement one-to-one conflict resolution and manual-review states for competing mappings
- [x] 5.7 Implement field comparison policies and difference classification for missing, redundant, attribute, structure, and duplicate cases
- [x] 5.8 Persist snapshot-bound difference evidence and expose paginated filter and detail REST endpoints
- [x] 5.9 Add accuracy and scale tests that prove candidate retrieval avoids all-pairs comparison

## 6. First Reconciliation Web Slice

- [ ] 6.1 Implement typed frontend API clients for uploads, task creation, task listing, task detail, and differences
- [ ] 6.2 Build the dashboard and reconciliation-task list with loading, empty, error, and retry states
- [x] 6.3 Build paired CSV upload and task-creation forms with source-role labels and server validation messages
- [ ] 6.4 Build task detail with ingestion, snapshot, normalization, matching, and difference stage status
- [ ] 6.5 Build the initial paginated difference table with entity-type and difference-type filters
- [ ] 6.6 Build a difference detail surface showing source value, target value, highlighted fields, organization context, and match evidence
- [ ] 6.7 Add frontend unit tests and a Playwright flow for upload through visible deterministic differences

## 7. Agent, Skills, MCP, and Mandatory Analysis

- [x] 7.1 Implement configurable external LLM and Embedding provider adapters with timeout, retry, and usage metadata
- [ ] 7.2 Define and validate structured Pydantic outputs for cause analysis, ambiguous matching, governance advice, rollback impact, and reports
- [x] 7.3 Create concise versioned Skills for difference analysis, ambiguous entity resolution, governance planning, rollback assessment, and reporting
- [x] 7.4 Implement a read-oriented MCP server with difference-context, candidate-search, mapping-rule, and execution-context tools
- [x] 7.5 Implement the governance Agent so it can use registered Skills and MCP tools but cannot mutate target data
- [x] 7.6 Implement mandatory per-difference analysis with deterministic explanations for clear cases and LLM fallback for semantic ambiguity
- [x] 7.7 Persist analysis cause, evidence, action, risk, confidence, model, Skill, prompt, tool trace, and timestamp provenance
- [x] 7.8 Enforce the backend rule that differences without valid analysis cannot enter an execution batch
- [x] 7.9 Add model-stub, invalid-output, retry-limit, MCP authorization, and manual-review tests
- [ ] 7.10 Extend the difference workbench to display analysis progress, cause, recommendation, risk, confidence, and disabled execution states

## 8. Governance Planning and Versioned CSV Execution

- [x] 8.1 Implement governance operation types for create, update, move, disable, skip, and manual review
- [x] 8.2 Implement plan building from selected analyzed differences without allowing model output to bypass field or operation policies
- [x] 8.3 Implement risk policy, dependency graph, topological ordering, and reversibility metadata
- [ ] 8.4 Add batch preview and confirmation APIs that bind the selected difference versions and authenticated operator
- [ ] 8.5 Implement preflight checks for target hash/version drift, expected before-values, dependencies, conflicts, and execution eligibility
- [ ] 8.6 Implement CSV target versioning that derives a new file and never overwrites the uploaded target
- [ ] 8.7 Implement per-operation execution, idempotency, partial failure, eligible retry, and append-only audit records
- [ ] 8.8 Implement target reload and expected-versus-actual verification after every applied operation
- [ ] 8.9 Add execution tests for dependency ordering, drift conflicts, partial failure, retries, failed verification, and original-file preservation

## 9. Batch Confirmation and Execution Frontend

- [ ] 9.1 Implement stable selection across paginated and filtered difference results
- [ ] 9.2 Build the batch confirmation view with exact create, update, move, disable, skip, and high-risk counts
- [ ] 9.3 Display preflight conflicts and require a fresh confirmation when the validated plan version changes
- [ ] 9.4 Build execution monitoring with per-operation state, partial failure, verification failure, and retry eligibility
- [ ] 9.5 Provide download access to derived Seewo CSV versions from successful or partially successful batches
- [ ] 9.6 Add frontend tests for disabled unanalyzed rows, stable selection, high-risk confirmation, partial failure, and retry actions

## 10. Execution History and Audit Views

- [ ] 10.1 Add execution history and execution-detail REST endpoints with stable pagination and immutable audit data
- [ ] 10.2 Return operator identity, task and snapshot references, plan version, before/after values, errors, retries, and verification results
- [ ] 10.3 Build execution history filters for task, operator, date, status, and rollback state
- [ ] 10.4 Build execution detail with operation table, audit timeline, source and output CSV versions, and permitted actions
- [ ] 10.5 Add API and UI tests proving client-supplied operator IDs cannot replace backend audit identity

## 11. Governance Reports and Rollback Compensation

- [ ] 11.1 Implement on-demand report jobs that read fixed execution-time facts and persist report versions
- [ ] 11.2 Implement a structured report containing snapshots, statistics, causes, selected plans, operator, outcomes, failures, and rollback status
- [ ] 11.3 Implement the initial HTML report renderer and downloadable report endpoint
- [ ] 11.4 Implement rollback preflight for later dependencies, target drift, reversibility, affected scope, and conflicting entities
- [ ] 11.5 Implement compensation-plan generation and validation without allowing the Agent to execute it directly
- [ ] 11.6 Execute approved rollback plans as new batches, verify the resulting target version, and link both execution records
- [ ] 11.7 Build report generation status, report viewer, rollback review, conflict explanation, and rollback confirmation views
- [ ] 11.8 Add tests for historical report consistency, blocked rollback, successful compensation, failed compensation, and immutable originals

## 12. Asynchronous Workflow and Performance

- [ ] 12.1 Add Redis and Celery only after the synchronous service-level workflow passes end-to-end tests
- [ ] 12.2 Wrap ingestion, reconciliation, AI analysis, execution, reporting, and rollback in idempotent Celery tasks
- [ ] 12.3 Persist stage transitions and progress in PostgreSQL so Celery state is never the source of truth
- [ ] 12.4 Implement SSE task and execution progress with reconnect cursors and polling fallback
- [ ] 12.5 Add bounded concurrency, provider rate limits, batch database writes, and embedding caches
- [ ] 12.6 Add generated large-CSV performance tests and document measured ingestion, matching, and analysis limits
- [ ] 12.7 Verify interrupted workers can retry safely without duplicate snapshots, operations, reports, or rollback batches

## 13. API Connector Readiness and Final Verification

- [x] 13.1 Add explicit not-configured Seewo and third-party API connector implementations that satisfy the connector interface without pretending production support
- [ ] 13.2 Add shared connector contract tests that run against CSV connectors and API test doubles
- [x] 13.3 Document how future API connectors must handle authentication, pagination, rate limits, source versions, mutation idempotency, and verification
- [ ] 13.4 Add representative synthetic CSV files covering all entity and difference types without real teacher or student data
- [ ] 13.5 Add end-to-end tests for the complete upload, reconcile, analyze, approve, execute, report, and rollback chain
- [ ] 13.6 Run backend tests, frontend tests, type checks, lint, production builds, OpenSpec validation, and desktop/mobile Playwright screenshots
- [ ] 13.7 Update `AGENTS.md` and developer documentation with the final real build, test, run, and data-fixture commands

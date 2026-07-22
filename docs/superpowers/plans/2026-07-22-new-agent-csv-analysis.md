# New Agent CSV Analysis Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the default-off, analysis-only CSV milestone for `new-agent-v1`: three-entity ingestion, deterministic identity evidence, bounded mandatory DeepSeek analysis, and durable findings without any target mutation.

**Architecture:** New Agent projections and repositories sit beside immutable legacy snapshots; legacy CSV task creation and matching remain unchanged. Deterministic services turn persisted CSV rows into identity postings, claims, work items, and silent correct correspondences before a separate leased model-batch service analyzes only actionable work. The Agent worker advances ingestion, identity construction, and analysis phases, then stops at the governance boundary because CSV execution remains disabled.

**Tech Stack:** Python 3.12, FastAPI domain services, Pydantic v2, SQLAlchemy 2 async, Alembic, PostgreSQL/SQLite tests, Polars CSV reader, HTTPX OpenAI-compatible DeepSeek provider, pytest, Ruff, mypy.

## Global Constraints

- New tasks expose only `department`, `student`, and `teacher`; class is an optional student attribute, never an identity entity.
- Third-party data is authoritative and read-only; no analysis component may propose or perform an authoritative mutation.
- Identity candidate keys are normalized number, phone, and email only. Name, category, and class never establish identity.
- A Seewo row with no identity key is marked and deterministically classified as target-extra; ingestion never deletes or edits it.
- Every actionable finding receives a Chinese category, evidence-backed analysis, server-validated risk, and one to three governance solutions; correct records remain user-facing silent.
- Model batches contain at most 50 persisted work items and use one initial call plus at most three retries, with no nested provider retry.
- Only `student.phone` is tokenized at new-Agent model boundaries; raw student phone never enters prompts, model-facing tools, logs, failures, or provenance.
- Model output is untrusted: exact membership, references, operation, fields, values, tokens, and solution counts are validated before an atomic commit.
- Input, mark, identity evidence/claim, work-item, finding, solution, dependency, and attempt/provenance rows are append-only; only fenced batch/lease coordination rows may change state.
- Model-result finalization validates both the batch lease and the owning Agent run/phase fencing token so a stale worker cannot persist attempts or findings.
- Invalid authoritative rows are excluded from identity lookup but still receive mandatory AI anomaly analysis; no solution may mutate authoritative data.
- Seeded analysis respects the selected department/student/teacher scope; unselected entities never enter indexes or model batches.
- `NEW_AGENT_ENABLED` remains default-off, `NEW_AGENT_ANALYSIS_ONLY` remains default-on, and CSV target mutation remains disabled.
- Existing `legacy-v1` snapshots, matching, differences, analyses, reports, restores, and deletion behavior remain readable and unchanged.
- Automated tests use synthetic fixtures and provider stubs. Real DeepSeek smoke is explicit opt-in and never prints secrets or payloads.

---

### Task 1: Durable CSV-analysis persistence and contract schemas

**Files:**
- Create: `backend/app/schemas/agent_ingestion.py`
- Create: `backend/app/schemas/agent_reconciliation.py`
- Create: `backend/app/models/agent_analysis.py`
- Create: `backend/app/repositories/agent_analysis.py`
- Create: `backend/alembic/versions/0020_agent_csv_analysis.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/tests/integration/test_migrations.py`
- Test: `backend/tests/unit/schemas/test_agent_contracts.py`
- Test: `backend/tests/integration/repositories/test_agent_analysis_repository.py`

**Interfaces:**
- Produces `AgentEntityKind`, `AgentSourceRole`, `AgentContractRecord`, `AgentInputMark`, `IdentityKeyKind`, `WorkItemKind`, `WorkItemState`, `AgentFindingPayload`, and `AgentSolutionPayload`.
- Produces append-only records for connector capabilities, normalized inputs/marks, identity postings/evidence/claims, work items, model batches/attempts, findings, solutions, and dependencies.
- Produces `AgentAnalysisRepository` idempotent persistence/list/claim/finalize methods used by Tasks 2–5.

- [ ] **Step 1: Write failing schema and repository tests** covering only three entity kinds, class applicability, one-to-three solutions, exact unique constraints, append-only records, idempotent replay, batch cardinality `1..50`, attempt cardinality `1..4`, and historical-row readability after migration.
- [ ] **Step 2: Run RED tests** with `/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest tests/unit/schemas/test_agent_contracts.py tests/integration/repositories/test_agent_analysis_repository.py tests/integration/test_migrations.py -q`; expect missing modules/tables.
- [ ] **Step 3: Implement strict Pydantic contracts** with `extra="forbid"`, frozen values, literal enums, stable locators/order, normalized identity fields, sanitized marks, typed evidence, findings, and solutions.
- [ ] **Step 4: Implement SQLAlchemy/Alembic persistence** with tenant/task/run/snapshot foreign keys, `(run_id, source_role, stable_order)` and stable-locator uniqueness, indexed normalized identity postings, deterministic idempotency hashes, batch/attempt constraints, and immutable hooks.
- [ ] **Step 5: Implement repository replay and fencing APIs** so duplicate input hashes return existing rows, mismatched hashes fail closed, completed batches cannot be overwritten, and attempt/finding/solution commit is atomic.
- [ ] **Step 6: Run GREEN tests**, Ruff, mypy, and the dedicated clean PostgreSQL migration smoke.
- [ ] **Step 7: Commit** with `feat: add durable agent csv analysis records`.

### Task 2: Three-entity CSV ingestion adapter and validation

**Files:**
- Create: `backend/app/ingestion/agent_contract.py`
- Create: `backend/app/ingestion/agent_csv_adapter.py`
- Modify: `backend/app/normalization/identifiers.py` only if a missing pure normalizer is required
- Test: `backend/tests/unit/ingestion/test_agent_contract.py`
- Test: `backend/tests/unit/ingestion/test_agent_csv_adapter.py`
- Test: `backend/tests/integration/agent_runtime/test_agent_ingestion_handler.py`

**Interfaces:**
- Consumes Task 1 contracts and repository.
- Produces `AgentCsvIngestionAdapter.inspect_pair(...) -> AgentIngestionOutcome` and `AgentIngestionPhaseHandler`.
- Reuses `inspect_csv`, `read_csv_frame`, file hashes, physical `_row_number`, existing stored uploads and immutable snapshot IDs without invoking `ReconciliationIngestionService.create_task()` or legacy `validate_frame()`.

- [ ] **Step 1: Write failing tests** for department/student/teacher projection, authoritative completeness (student alone requires class), target rows with only email, no-key target-extra marking, selected-entity scope, UTF-8/GB18030, stable physical row order, malformed/unmappable schemas, and replay idempotency.
- [ ] **Step 2: Run RED tests** and confirm failures are missing Agent adapter behavior rather than legacy regressions.
- [ ] **Step 3: Implement contract mapping** for canonical columns `category,name,number,class,phone,email`, accepted Chinese/English entity labels, normalized identity values, and non-student `class_name=None`.
- [ ] **Step 4: Implement role-specific marks**: incomplete authority rows are immutable/excluded; no-key target rows are immutable/marked but retained as target-extra candidates; target category/name/class absence is downstream work.
- [ ] **Step 5: Persist capability snapshot, input projections, marks and safe phase events/checkpoint** without writing raw phone to marks, event payloads, quarantine summaries, or logs.
- [ ] **Step 6: Run GREEN tests** plus all existing ingestion and legacy API tests.
- [ ] **Step 7: Commit** with `feat: add agent csv ingestion adapter`.

### Task 3: Deterministic identity indexes, claims, and work items

**Files:**
- Create: `backend/app/reconciliation/agent_identity.py`
- Create: `backend/app/reconciliation/agent_work_items.py`
- Test: `backend/tests/unit/reconciliation/test_agent_identity.py`
- Test: `backend/tests/integration/repositories/test_agent_identity_repository.py`
- Test: `backend/tests/integration/agent_runtime/test_agent_identity_handler.py`

**Interfaces:**
- Consumes included Task 1 input records.
- Produces `AgentIdentityIndexBuilder.build(run_id)`, `AgentWorkItemBuilder.build(run_id)`, and `AgentIdentityPhaseHandler`.
- Persists all candidate-key evidence and deterministic claims before any model invocation.

- [ ] **Step 1: Write failing tests** for normalized exact postings, duplicate authority hits, contradictory keys, student→teacher→department search evidence, missing-number recovery by phone/email, earliest stable target claim, later duplicate target, no-hit target-extra, unclaimed authority target-missing, invalid authority exclusion plus anomaly work, all six ordinary-field difference cases, and correct-row silence.
- [ ] **Step 2: Run RED tests** and record missing index/work-item behavior.
- [ ] **Step 3: Implement task/snapshot/entity-partitioned ordinary indexes** over number/phone/email without vector/embedding imports or matching-quality calls.
- [ ] **Step 4: Implement deterministic candidate evaluation** that retains every hit, treats cross-key or multi-hit evidence as conflict, and never lets search order choose contradictory evidence.
- [ ] **Step 5: Implement stable claims and terminal work items** in connector order; correct resolved rows store only correspondence evidence, while field differences, duplicates, target-extra, target-missing, invalid-authority anomaly, and conflicts receive typed work.
- [ ] **Step 6: Run GREEN tests** plus legacy matching/rematching characterization tests to prove isolation.
- [ ] **Step 7: Commit** with `feat: add deterministic agent identity work`.

### Task 4: Bounded DeepSeek analysis, privacy, and strict validation

**Files:**
- Create: `backend/app/ai/agent_batching.py`
- Create: `backend/app/ai/agent_analysis.py`
- Create: `backend/app/ai/agent_phone_privacy.py`
- Create: `backend/app/ai/agent_prompting.py`
- Modify: `backend/app/ai/providers/llm.py`
- Modify: `backend/app/ai/skills/contracts.py`
- Modify: `backend/app/ai/skills/reconcile-entity-batch/SKILL.md`
- Modify: `backend/app/ai/mcp/agent_authorization.py`
- Test: `backend/tests/unit/ai/test_agent_batching.py`
- Test: `backend/tests/unit/ai/test_agent_phone_privacy.py`
- Test: `backend/tests/unit/ai/test_agent_analysis_validation.py`
- Test: `backend/tests/integration/repositories/test_agent_model_batches.py`

**Interfaces:**
- Consumes actionable Task 3 work items and Task 1 repository.
- Produces deterministic `partition_analysis_batches(..., max_items=50)`, `StudentPhoneTokenizationContext`, `AgentAnalysisService.analyze_claimed_batch(...)`, and strict response validation.
- Provider offers an explicit single-transport-attempt mode so outer durable retry owns exactly four total attempts.

- [ ] **Step 1: Write failing tests** for `50/51` partitioning, exact response membership, duplicate/omitted/forged IDs, Chinese category, one-to-three solutions with exactly one recommendation, allowed operations/evidence/values, authority-write rejection, invalid-authority anomaly analysis, business-correct create/delete/retain semantics, and no partial commit.
- [ ] **Step 2: Write failing privacy/security tests** proving student phone is task-scoped tokenized, teacher phone follows the new non-sensitive policy, raw/unknown/cross-item tokens are rejected, injection text remains inside an `untrusted_evidence` envelope, and capabilities cannot expand.
- [ ] **Step 3: Run RED tests** and confirm legacy tokenization/provider tests remain unaffected.
- [ ] **Step 4: Implement deterministic batch manifests and prompt envelopes**, with fixed Skill instructions and server-owned response schema; remove model-side `persist_finding` permission so the service commits only after full validation.
- [ ] **Step 5: Implement student-phone invocation tokenization** using task-scoped HMAC, issued-token allowlists, raw-phone rejection, and no secret/payload logging.
- [ ] **Step 6: Implement durable analysis service** with single-attempt provider calls inside one-initial-plus-three retry orchestration (exactly four transport calls on exhaustion), sanitized append-only attempt provenance, Agent-run plus batch fencing, atomic finding/solution finalization, and blocked-model failure on exhaustion without releasing the school lock.
- [ ] **Step 7: Run GREEN tests**, provider regression tests, security tests, Ruff, and mypy.
- [ ] **Step 8: Commit** with `feat: add bounded agent model analysis`.

### Task 5: CSV analysis-only orchestration and end-to-end verification

**Files:**
- Create: `backend/app/agent_runtime/csv_analysis_handlers.py`
- Create: `backend/app/agent_runtime/csv_analysis_worker.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/agent_runtime/README.md`
- Modify: `backend/README.md` or the existing backend runbook location
- Modify: `openspec/changes/new-agent-architecture/tasks.md`
- Test: `backend/tests/integration/agent_runtime/test_csv_analysis_pipeline.py`
- Test: `backend/tests/integration/ai/test_deepseek_csv_analysis_smoke.py`
- Test: `backend/tests/integration/test_migrations.py`

**Interfaces:**
- Composes Tasks 2–4 into AgentWorker handlers for `INGEST_AND_NORMALIZE`, `BUILD_IDENTITY_WORK`, `ANALYZE_BATCHES`, and the analysis-only governance boundary.
- No public task API is added; task 10.1 remains pending. Synthetic integration tests seed `new-agent-v1` tasks/runs directly.

- [ ] **Step 1: Write a failing synthetic pipeline test** that seeds paired CSV uploads, runs all analysis handlers, verifies marks/indexes/claims/work items/batches/findings/events/checkpoints, keeps correct rows silent, and proves no governance plan/execution/target version is created.
- [ ] **Step 2: Write failing recovery tests** for duplicate phase delivery, restart after ingestion/identity/batch completion, lease loss, one failed batch, analysis-only stop, school-lock retention, and legacy worker isolation.
- [ ] **Step 3: Implement handler composition and configuration validation** for `batch_max=50`, exactly three retries after initial call, tokenization secret, DeepSeek gateway readiness, feature flags, and analysis-only no-write invariant.
- [ ] **Step 4: Implement explicit analysis-only terminal boundary**: conflicts wait in clarification; otherwise the run records analysis completion and waits before governance without compiling/executing operations.
- [ ] **Step 5: Add opt-in real DeepSeek smoke** guarded by `RECONCILIATION_RUN_DEEPSEEK_SMOKE=1`, using one synthetic item and no real records; never print request/response/headers/secrets/raw phone.
- [ ] **Step 6: Update OpenSpec checkboxes only for individually and fully delivered tasks.** Never blanket-check 6.1–6.9: leave clarification/approval/report obligations such as 6.5 and any unmet fallback obligation such as 6.8 pending; also leave reports, approvals, execution, public API, frontend, API/database connectors and broad all-phase worker tasks pending.
- [ ] **Step 7: Run full verification**: backend pytest, dedicated clean PostgreSQL migration, Ruff, mypy, OpenSpec strict validation, and opt-in DeepSeek smoke if configuration is complete.
- [ ] **Step 8: Request whole-branch code review**, fix every Critical/Important finding, rerun affected and full gates, and leave the worktree unmerged for user approval.

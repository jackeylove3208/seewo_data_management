## 1. Baseline and contracts

- [ ] 1.1 Add a regression fixture reproducing the current class-name mismatch, absent student numbers, 473 obvious student counterparts, and the resulting unmatched cascade; record focused backend/frontend baselines before implementation
- [x] 1.2 Add Pydantic schemas for versioned OR-of-AND key policies, key-group evidence, trusted cross-system identifiers, rematching decisions, candidate edges, job progress, and matching quality results
- [x] 1.3 Add failing contract tests for complete/partial key groups, unique/conflicting matches, server-owned candidate IDs, Chinese manual fallbacks, counter consistency, and quality-gate validation
- [x] 1.4 Add configuration for rematching feature/shadow mode, Top-K default 3, high-confidence threshold, worker lease/concurrency/retry, and versioned quality thresholds without exposing model credentials

## 2. Robust initial entity resolution

- [x] 2.1 Refactor exact matching to evaluate ordered alternative key groups, require all fields inside a group, and persist the winning group/version as evidence
- [x] 2.2 Implement deterministic conflict handling when one group is non-unique or complete groups identify different targets, with tests for shared contacts and duplicate identifiers
- [x] 2.3 Add explicit source-pair trust policy for `source_id`, default it to disabled, and test trusted, untrusted, and cross-tenant cases
- [x] 2.4 Implement strict-then-relaxed candidate blocking so unresolved parents cannot reduce child candidates to zero, while preventing name-only automatic acceptance
- [x] 2.5 Recompute descendant context after recovered organization/class mappings and add hierarchy regression tests for classes, students, teachers, and memberships
- [x] 2.6 Implement maximum-confidence one-to-one assignment with pinned confirmed mappings, deterministic tie handling, losing-edge conflicts, and cardinality tests

## 3. Bidirectional snapshot vector index

- [x] 3.1 Add role-aware snapshot embedding models and an Alembic migration that backfills or compatibly reads existing target embeddings without losing provider/model/version provenance
- [x] 3.2 Generalize the embedding repository and `VectorIndex` to idempotently index authoritative and target snapshots and query only the opposite role within tenant, snapshot pair, and entity type
- [x] 3.3 Build versioned entity representations from allowed canonical fields, local similarity features, and governed tokenization; verify protected raw values are absent from external requests and persisted diagnostics
- [x] 3.4 Implement strict and relaxed bidirectional Top-3 retrieval, union/deduplicate candidate edges, and test target-to-source recovery of an apparent Seewo-redundant entity
- [x] 3.5 Add scale tests proving candidate generation remains bounded and never evaluates or sends the full source/target Cartesian product

## 4. Durable AI rematching

- [x] 4.1 Add `entity_rematch_jobs`, work items, candidate-edge persistence, mapping supersession references, indexes, constraints, and reversible migration coverage
- [x] 4.2 Implement tenant-scoped repositories for idempotent job creation, one-item leases, heartbeat, retry/backoff, lease recovery, cancellation, counters, and immutable outcome reuse
- [x] 4.3 Add an entity-rematching Skill, read-only MCP candidate evidence tool, tokenized prompt builder, and structured LLM contract limited to accept-candidate, no-match, or manual-review
- [x] 4.4 Implement policy validation for candidate membership, two-feature corroboration, confidence, relationship evidence, current snapshot/mapping versions, and safe Chinese fallback
- [x] 4.5 Implement a worker that claims and commits leases before external calls, runs without an open database transaction, independently persists each outcome, and survives per-item failures
- [ ] 4.6 Build the candidate graph from deterministic and AI-validated edges, run the one-to-one assignment, and append current mapping decisions without rewriting historical decisions
- [x] 4.7 Add create/get/retry/cancel and SSE/polling APIs with authenticated tenant isolation, monotonic progress, refresh recovery, and stable localized errors

## 5. Matching quality gates

- [x] 5.1 Implement full-task per-entity aggregation for initial accepted, AI-recovered, manual-review, conflict, unmatched, unconsumed-target, and predicted missing/redundant counts
- [x] 5.2 Implement the versioned default gate policy for unresolved ratio, zero accepted parents, and anomalous create/disable volume, including the minimum population rule
- [ ] 5.3 Persist gate results bound to current mapping versions and reject stale results when any mapping is superseded
- [x] 5.4 Make difference detection require a passing current gate and prove a blocked task creates no formal differences or governance analysis jobs
- [ ] 5.5 Add retry/re-evaluation after rematching retry or operator mapping confirmation, preserving prior audit records and recomputing descendants

## 6. Workflow integration

- [ ] 6.1 Update workflow orchestration so initial matching creates/reuses a durable rematching job when unresolved data exists and returns without waiting for model calls
- [ ] 6.2 Skip rematching when every mapping is accepted, wait while rematching is non-terminal, evaluate quality when terminal, and only then advance to formal differences
- [ ] 6.3 Extend persisted workflow/task projections with rematching job ID, initial unresolved, indexed, processed, recovered, manual, conflict, failed, recent-update, and gate details
- [ ] 6.4 Add idempotency, concurrent advancement, process restart, stale mapping, gate failure, and safe retry integration tests across matching through difference detection

## 7. Frontend matching recovery experience

- [x] 7.1 Add TypeScript contracts, API clients, query keys, SSE observation, two-second polling fallback, refresh resume, retry, and cancel hooks for rematching jobs and quality summaries
- [x] 7.2 Extend the existing entity-resolution stage with stable sub-progress for initial matching, vector indexing, AI recovery, global assignment, recent updates, and reduced-motion behavior
- [x] 7.3 Show Chinese counts for initial unresolved, AI-recovered, remaining manual/conflict, and failures while hiding difference/governance summaries until the gate passes
- [x] 7.4 Add an actionable quality-gate state showing affected entity types, observed metrics, thresholds, reasons, and retry/manual-mapping entries without claiming source data was modified
- [x] 7.5 Add Testing Library coverage for no-rematch fast path, running recovery, refresh resume, successful recovery, manual fallback, gate block, zero-candidate, and localized error states

## 8. End-to-end verification and rollout

- [x] 8.1 Add fake enterprise embedding/LLM tests proving dual-role isolation, Top-3 bounds, invented-ID rejection, tokenization, retry/fallback, and no source-system writes
- [ ] 8.2 Add PostgreSQL integration coverage for migrations, concurrent leases, candidate uniqueness, one-to-one assignment, mapping supersession, and gate/version races
- [ ] 8.3 Add an end-to-end regression from the supplied paired CSV shape through class recovery, obvious student recovery, bounded remaining differences, and no mass high-risk student output
- [ ] 8.4 Run shadow mode against synthetic large fixtures, record recovery/false-candidate/gate metrics, and confirm accepted initial mappings create no model requests
- [ ] 8.5 Verify desktop/mobile progress and gate layouts with Playwright screenshots, long Chinese text, reduced motion, refresh recovery, and no overlapping controls
- [ ] 8.6 Run backend pytest, Ruff check/format check, mypy, pip check, migration upgrade/downgrade/re-upgrade, frontend Vitest, ESLint, TypeScript, production build, Playwright, and `openspec validate add-ai-find`
- [ ] 8.7 Update worker, migration, feature-flag, threshold, gateway, and rollback documentation without placing real credentials or organization data in tracked files

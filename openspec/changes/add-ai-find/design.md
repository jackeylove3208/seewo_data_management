## Context

The current pipeline resolves hierarchy parents first, then uses stable identifiers, strict context blocking, lexical/vector candidate retrieval, and scoring. In the observed CSV pair, student numbers were absent and class names used different conventions. No classes were accepted, authoritative students therefore had no resolved parent while target students had target class IDs, strict blocks returned no candidates, and hundreds of otherwise identical students became unmatched. Difference detection correctly amplified those mappings into missing/redundant records, and governance analysis correctly classified mass disable operations as high risk.

The repository already provides immutable snapshots, versioned normalization and mapping evidence, a target-side embedding cache, an enterprise LLM gateway with tokenization, MCP read tools, durable AI analysis jobs, and immutable differences. The change must reuse these boundaries, preserve tenant isolation and auditability, and complete rematching before formal immutable differences are created.

## Goals / Non-Goals

**Goals:**

- Match entities deterministically through alternative complete key groups instead of one required field.
- Prevent unresolved parent mappings from eliminating every child candidate.
- Index both snapshot roles and recover unresolved pairs with bounded bidirectional Top-3 retrieval and LLM adjudication.
- Preserve accepted initial mappings and use the model only for unresolved data.
- Enforce one-to-one assignment and block abnormal mapping output before difference detection.
- Expose durable progress, Chinese explanations, retries, and complete provenance.

**Non-Goals:**

- Do not infer arbitrary CSV schemas or replace the existing governed field-mapping profiles in this change.
- Do not compare full source/target Cartesian products or send whole CSV files to a model.
- Do not let the LLM invent entity IDs, bypass field evidence, or directly update snapshots or external systems.
- Do not use governance-analysis results to repair mappings after formal differences are committed.
- Do not automatically accept a candidate supported only by semantic name similarity.

## Decisions

### 1. Model alternative keys as OR-of-AND policy groups

Each entity type receives a versioned ordered list of groups. Every field inside one group is conjunctive; groups are alternatives. Initial defaults will include strong identifiers first and corroborated composites later, for example student number OR `name + phone` OR `name + email` OR `name + resolved class`. Platform `source_id` is candidate evidence unless an explicit source-pair profile marks it as a shared business key.

This satisfies “one valid combination is enough” without treating any matching word as identity proof. A group must be complete, normalized, policy-allowed, and unique. If groups disagree, the result is a conflict. Deterministic and confirmed historical matches remain ahead of fuzzy/AI processing.

### 2. Use strict-then-relaxed candidate blocks

Candidate retrieval first uses tenant, entity type, campus, and resolved parent. If no candidate exists because the authoritative parent is unresolved, retrieval widens only to the same tenant and entity type, with available grade/campus hints. Parent agreement becomes a score and risk feature rather than a hard prerequisite in that fallback.

This avoids the current `None` versus target-parent-UUID cascade while preserving efficient strict blocks for normal data. A relaxed-block candidate cannot auto-match without independent strong evidence.

### 3. Generalize the embedding cache to both snapshot roles

Replace the target-specific embedding ownership boundary with a generic snapshot entity embedding keyed by tenant, snapshot, source role, entity type, entity ID, representation version, provider, and model. One physical table and repository expose role-filtered indexes for both authoritative and target snapshots; this is operationally simpler than two database services while preserving bidirectional isolation.

Initial resolution can continue using target retrieval. Rematching builds missing embeddings for unresolved entities in both roles, performs source-to-target and target-to-source Top-3 queries, unions and deduplicates candidate edges, and stores scores and representation provenance. Exact and local lexical features remain available even when the external embedding provider is unavailable.

### 4. Add a separate durable rematching job

Create `entity_rematch_jobs`, `entity_rematch_items`, and persisted candidate-edge records rather than overloading difference analysis jobs. Work items are bound to source/target snapshots and current mapping versions. A worker claims one focal entity with a lease, loads its persisted Top-3 candidates, releases the transaction, calls the enterprise model, and commits one terminal outcome in a short transaction.

The model sees a schema-limited candidate list, task-tokenized protected fields, locally computed similarities, relationship evidence, and stable candidate IDs. It returns accept-candidate, no-match, or manual-review in Simplified Chinese. Invalid IDs, low evidence, gateway exhaustion, or policy failures become manual-review outcomes rather than fabricated matches.

### 5. Resolve a global candidate graph before accepting mappings

Validated deterministic and AI candidate decisions form weighted edges. Accepted mappings are selected using a maximum-weight one-to-one assignment per entity type, with confirmed mappings pinned. Auto-accept additionally requires the configured high-confidence threshold and at least two independent strong evidence features. Losing or near-tied edges become conflicts.

This prevents separately evaluated students from silently selecting the same Seewo record. Bidirectional retrieval improves graph recall but does not itself prove identity.

### 6. Recompute hierarchy context inside the matching stage

Rematching follows organization units, classes, teachers/students, then memberships. When a parent mapping is recovered, descendant normalized context and candidate blocks are recomputed before descendant work is finalized. The system does not create formal differences until every current rematching work item is terminal and the quality gate passes.

This keeps immutable differences final. Performing repair after difference persistence was rejected because it would require superseding large difference sets and could leave governance analysis bound to stale mappings.

### 7. Gate difference detection with versioned quality policy

Persist a quality result per task and entity type containing all initial and recovered mapping counters plus predicted missing/redundant volume. The initial policy blocks when at least 10 entities exist and unresolved ratio exceeds 20 percent, when dependent children exist but zero parents are accepted, or when predicted create/disable ratios exceed the same configured safety threshold. Thresholds and policy version are configuration, not client input.

A failed gate uses `matching_quality_gate_failed`, exposes Chinese remediation, and is retryable after manual mapping confirmation or rematching retry. Difference detection revalidates mapping versions and the gate version immediately before committing.

### 8. Extend the task page without adding a new top-level workflow stage

The existing “实体解析” stage shows sub-progress for initial matching, vector indexing, AI recovery, global assignment, and quality evaluation. It displays initial unresolved, AI-recovered, remaining manual/conflict, and recent-update counts. This keeps the four-stage user model while making the recovery process observable.

## Risks / Trade-offs

- [Relaxed retrieval increases false candidates] -> Cap at Top 3, require multiple strong features, restrict IDs to server candidates, and route ambiguity to manual review.
- [External embeddings cannot safely use all raw personal data] -> Apply enterprise gateway policy and tokenization, prefer local lexical/exact features for protected values, persist no raw model payloads, and make representation versions auditable.
- [LLM calls for hundreds of unresolved entities are slow or costly] -> Skip accepted mappings, batch index operations, run leased work items concurrently within limits, cache candidate edges, and allow deterministic recovery before model calls.
- [Global assignment changes an individually preferred candidate] -> Persist winning and losing edge evidence and expose conflicts instead of silently lowering confidence.
- [Quality thresholds block legitimate large migrations] -> Version and configure thresholds, report exact observed metrics, and allow authorized manual confirmation/retry without bypassing audit.
- [Generalizing the target embedding table affects existing matching] -> Provide a compatibility migration and repository adapter, verify old target searches before switching writes, and retain rollback until both paths agree.

## Migration Plan

1. Add alternative key-policy schemas and tests while retaining existing defaults behind the current rule version.
2. Create the generic role-aware embedding table and backfill existing target embedding metadata; keep target-side reads compatible during rollout.
3. Add rematching job/item/candidate tables, worker, structured LLM contract, MCP evidence tools, and APIs behind a disabled feature flag.
4. Add quality summaries and gate evaluation, then enable shadow mode to measure recovered mappings without changing current decisions.
5. Enable rematching for failed initial mappings, recompute hierarchy context, and enforce the gate before formal differences.
6. Enable task-page progress and recovery controls, then remove the legacy target-only embedding write path after comparison tests pass.

Rollback disables rematching and gate enforcement first, stops rematching workers, and restores target-only embedding reads. Append-only rematching and mapping audit records remain for investigation; new tables are dropped only after confirming no active task references them.

## Open Questions

- Production thresholds may need per-tenant tuning after shadow-mode measurements; the initial default remains 20 percent with a minimum population of 10.
- Whether a tenant may explicitly trust cross-system `source_id` equality is a field-mapping policy decision and defaults to disabled.

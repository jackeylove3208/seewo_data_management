# Organization Entity Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve third-party organization entities to Seewo entities at scale using normalization, historical mappings, deterministic keys, bounded lexical/vector candidates, explainable scoring, and manual-review conflicts.

**Architecture:** Process entity types in dependency order: organization units, classes, teachers, students, then memberships. The resolver uses confirmed mappings and exact identifiers first; unresolved entities are partitioned into compatible blocks, receive bounded lexical and embedding candidates, then pass entity-specific deterministic scoring and one-to-one cardinality checks. Embeddings retrieve candidates but never decide a match by themselves; LLM ambiguity handling belongs to module 4.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2, PostgreSQL + pgvector, RapidFuzz, Unicode standard library, pytest.

## Global Constraints

- Consume only published canonical snapshot pairs from module 1.
- Never compare entities across tenant, entity type, scope, or incompatible campus/parent blocks.
- Match order is `organization_unit -> class -> teacher -> student -> membership`.
- Confirmed historical mappings outrank exact, lexical, vector, and AI evidence until revoked.
- Exact matches require validated entity-specific identifiers or composite keys; names alone are not exact identifiers.
- Vector similarity is candidate-retrieval evidence only.
- Automatic acceptance requires score and first-to-second margin thresholds; otherwise status is `MANUAL_REVIEW`.
- Two authoritative entities may not silently consume the same target entity.
- Persist method, evidence, confidence, rule version, and confirmation provenance for every accepted or review decision.

---

## File Map

- `backend/app/normalization/`: pure rules and versioned pipelines.
- `backend/app/matching/`: exact lookup, partitions, candidate retrieval, scoring, conflict resolution, and orchestration.
- `backend/app/ai/providers/base.py`: embedding protocol shared with later external adapters.
- `backend/app/models/mappings.py` and `repositories/mappings.py`: mapping history, revocation, candidates, and decisions.
- `backend/app/schemas/matching.py`: stable cross-module matching contracts.

### Task 1: Implement pure primitive normalization

**Files:**
- Create: `backend/app/normalization/text.py`
- Create: `backend/app/normalization/identifiers.py`
- Test: `backend/tests/unit/normalization/test_primitives.py`

**Interfaces:**
- Consumes: `str | None` source values.
- Produces: `normalize_unicode`, `normalize_whitespace`, `normalize_null`, `normalize_phone`, `normalize_email`, `normalize_identifier`, and `normalize_status` pure functions.

- [ ] **Step 1: Write table-driven failing tests**

```python
import pytest
from app.normalization.identifiers import normalize_email, normalize_identifier, normalize_phone
from app.normalization.text import normalize_null, normalize_whitespace

@pytest.mark.parametrize(("raw", "expected"), [(" 张  三 ", "张 三"), ("Ａ班", "A班"), (None, None)])
def test_whitespace_and_nfkc(raw, expected) -> None:
    assert normalize_whitespace(raw) == expected

def test_identifiers_are_not_fuzzy() -> None:
    assert normalize_identifier(" e-007 ") == "E-007"
    assert normalize_phone("+86 138-0000-0000") == "13800000000"
    assert normalize_email(" Teacher@Example.COM ") == "teacher@example.com"
    assert normalize_null(" N/A ") is None
```

- [ ] **Step 2: Run the primitive tests**

Run: `cd backend && uv run pytest tests/unit/normalization/test_primitives.py -q`

Expected: FAIL because normalization modules do not exist.

- [ ] **Step 3: Implement null-safe Unicode and identifier rules**

```python
# backend/app/normalization/text.py
import re
import unicodedata

NULL_TOKENS = {"", "null", "none", "n/a", "-"}

def normalize_unicode(value: str | None) -> str | None:
    return unicodedata.normalize("NFKC", value) if value is not None else None

def normalize_whitespace(value: str | None) -> str | None:
    normalized = normalize_unicode(value)
    return re.sub(r"\s+", " ", normalized).strip() if normalized is not None else None

def normalize_null(value: str | None) -> str | None:
    normalized = normalize_whitespace(value)
    return None if normalized is None or normalized.casefold() in NULL_TOKENS else normalized
```

```python
# backend/app/normalization/identifiers.py
import re
from app.normalization.text import normalize_null

def normalize_identifier(value: str | None) -> str | None:
    normalized = normalize_null(value)
    return normalized.upper() if normalized else None

def normalize_phone(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", normalize_null(value) or "")
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    return digits or None

def normalize_email(value: str | None) -> str | None:
    normalized = normalize_null(value)
    return normalized.casefold() if normalized else None
```

- [ ] **Step 4: Verify and commit**

Run: `cd backend && uv run pytest tests/unit/normalization/test_primitives.py -q && uv run ruff check app/normalization`

Expected: all table cases PASS.

```bash
git add backend/app/normalization backend/tests/unit/normalization
git commit -m "feat: add pure entity normalization rules"
```

### Task 2: Add configurable organization-aware normalization pipeline

**Files:**
- Create: `backend/app/normalization/organization.py`
- Create: `backend/app/normalization/pipeline.py`
- Create: `backend/app/normalization/rules.v1.json`
- Test: `backend/tests/unit/normalization/test_pipeline.py`

**Interfaces:**
- Consumes: `CanonicalEntity` and `NormalizationConfig`.
- Produces: `NormalizedEntity(entity, normalized, warnings, rule_version)` with organization path, grade, school year, class number, and teacher display name fields.

- [ ] **Step 1: Write domain normalization tests**

```python
def test_class_variants_share_comparable_fields(pipeline, class_factory) -> None:
    left = pipeline.normalize(class_factory(name="高一(1)班", school_year="2024"))
    right = pipeline.normalize(class_factory(name="2024级1班", school_year="2024"))
    assert left.normalized["class_number"] == right.normalized["class_number"] == "1"
    assert left.rule_version == "normalization-v1"

def test_teacher_subject_suffix_is_evidence_not_data_loss(pipeline, teacher_factory) -> None:
    result = pipeline.normalize(teacher_factory(name="张三（语文）"))
    assert result.normalized["display_name"] == "张三"
    assert result.normalized["subject_hint"] == "语文"
```

- [ ] **Step 2: Run pipeline tests**

Run: `cd backend && uv run pytest tests/unit/normalization/test_pipeline.py -q`

Expected: FAIL because `NormalizationPipeline` is missing.

- [ ] **Step 3: Implement versioned configuration and pipeline**

```python
class NormalizationConfig(BaseModel):
    version: str = "normalization-v1"
    path_separators: tuple[str, ...] = ("/", ">", "\\")
    teacher_subjects: frozenset[str] = frozenset({"语文", "数学", "英语", "物理", "化学"})

class NormalizedEntity(BaseModel):
    entity: CanonicalEntity
    normalized: dict[str, str | None]
    warnings: tuple[str, ...] = ()
    rule_version: str

class NormalizationPipeline:
    def __init__(self, config: NormalizationConfig) -> None:
        self.config = config

    def normalize(self, entity: CanonicalEntity) -> NormalizedEntity:
        values = entity.model_dump()
        normalized = normalize_entity_fields(entity.entity_type, values, self.config)
        return NormalizedEntity(entity=entity, normalized=normalized, rule_version=self.config.version)
```

- [ ] **Step 4: Verify every rule and commit**

Run: `cd backend && uv run pytest tests/unit/normalization -q`

Expected: Unicode, null, phone, email, ID, status, path, grade, year, class-number, and teacher-display tests PASS.

```bash
git add backend/app/normalization backend/tests/unit/normalization
git commit -m "feat: add versioned organization normalization"
```

### Task 3: Persist mappings, candidates, and confirmation provenance

**Files:**
- Create: `backend/app/schemas/matching.py`
- Create: `backend/app/models/mappings.py`
- Create: `backend/app/repositories/mappings.py`
- Create: `backend/alembic/versions/0002_entity_resolution.py`
- Test: `backend/tests/integration/repositories/test_mappings.py`

**Interfaces:**
- Consumes: source/target entity IDs, snapshots, evidence, scores, operator or system confirmer.
- Produces: `MatchDecision`, `MappingRepository.find_confirmed`, `save_decision`, `revoke`, and active-pair uniqueness.

- [ ] **Step 1: Write history and revocation tests**

```python
async def test_confirmed_mapping_is_reused_until_revoked(mapping_repo, pair) -> None:
    saved = await mapping_repo.confirm(pair, confirmed_by="operator-1")
    assert await mapping_repo.find_confirmed(pair.tenant_id, pair.source_key) == saved
    await mapping_repo.revoke(saved.id, revoked_by="operator-2", reason="wrong person")
    assert await mapping_repo.find_confirmed(pair.tenant_id, pair.source_key) is None

async def test_target_cannot_have_two_active_confirmed_sources(mapping_repo, pair, second_pair) -> None:
    await mapping_repo.confirm(pair, confirmed_by="operator-1")
    with pytest.raises(MappingCardinalityError):
        await mapping_repo.confirm(second_pair, confirmed_by="operator-1")
```

- [ ] **Step 2: Run mapping repository tests**

Run: `cd backend && uv run pytest tests/integration/repositories/test_mappings.py -q`

Expected: FAIL because mapping persistence is absent.

- [ ] **Step 3: Define stable matching contracts**

```python
class MatchMethod(StrEnum):
    HISTORICAL = "historical"
    STABLE_ID = "stable_id"
    COMPOSITE_KEY = "composite_key"
    SCORED = "scored"

class MatchStatus(StrEnum):
    ACCEPTED = "accepted"
    MANUAL_REVIEW = "manual_review"
    UNMATCHED = "unmatched"
    CONFLICT = "conflict"

class MatchEvidence(BaseModel):
    feature: str
    source_value: str | None
    target_value: str | None
    score: float = Field(ge=0, le=1)

class MatchDecision(BaseModel):
    source_entity_id: UUID
    target_entity_id: UUID | None
    method: MatchMethod | None
    status: MatchStatus
    confidence: float = Field(ge=0, le=1)
    evidence: tuple[MatchEvidence, ...]
    rule_version: str
    confirmed_by: str | None = None
```

- [ ] **Step 4: Add append-only history and active uniqueness**

```python
class EntityMapping(Base, TimestampMixin):
    __tablename__ = "entity_mappings"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(index=True)
    source_entity_id: Mapped[UUID] = mapped_column(ForeignKey("canonical_entities.id"), index=True)
    target_entity_id: Mapped[UUID] = mapped_column(ForeignKey("canonical_entities.id"), index=True)
    method: Mapped[str]
    confidence: Mapped[Decimal]
    evidence: Mapped[list] = mapped_column(JSONB)
    rule_version: Mapped[str]
    confirmed_by: Mapped[str | None]
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[str | None]
    revocation_reason: Mapped[str | None]
```

Migration adds partial unique indexes on `(tenant_id, source_entity_id)` and `(tenant_id, target_entity_id)` where `revoked_at IS NULL AND confirmed_by IS NOT NULL`.

- [ ] **Step 5: Migrate, verify, and commit**

Run: `cd backend && uv run alembic upgrade head && uv run pytest tests/integration/repositories/test_mappings.py -q`

Expected: reuse, revocation, and cardinality tests PASS.

```bash
git add backend/app/schemas/matching.py backend/app/models/mappings.py backend/app/repositories/mappings.py backend/alembic backend/tests/integration/repositories/test_mappings.py
git commit -m "feat: persist entity mapping provenance"
```

### Task 4: Implement historical and exact matching

**Files:**
- Create: `backend/app/matching/exact_matcher.py`
- Test: `backend/tests/unit/matching/test_exact_matcher.py`

**Interfaces:**
- Consumes: normalized source entity, compatible targets, and confirmed mapping lookup.
- Produces: accepted `MatchDecision` or `None`; key policy is explicit per entity type.

- [ ] **Step 1: Write stable-ID and composite-key tests**

```python
def test_teacher_employee_number_beats_name(exact_matcher, normalized_teacher, target_teachers) -> None:
    decision = exact_matcher.match(normalized_teacher(employee_number="E007"), target_teachers)
    assert decision.method is MatchMethod.STABLE_ID
    assert decision.evidence[0].feature == "employee_number"

def test_duplicate_stable_id_routes_to_review(exact_matcher, duplicated_targets) -> None:
    decision = exact_matcher.match(source_teacher(employee_number="E007"), duplicated_targets)
    assert decision.status is MatchStatus.CONFLICT

def test_name_alone_is_not_exact(exact_matcher) -> None:
    assert exact_matcher.match(source_teacher(name="张三"), [target_teacher(name="张三")]) is None
```

- [ ] **Step 2: Run exact matching tests**

Run: `cd backend && uv run pytest tests/unit/matching/test_exact_matcher.py -q`

Expected: FAIL because exact matcher does not exist.

- [ ] **Step 3: Implement entity-specific key policies**

```python
STABLE_KEYS = {
    EntityType.ORGANIZATION_UNIT: (("code",),),
    EntityType.CLASS: (("source_id",), ("school_year", "grade", "class_number", "parent_mapping_id")),
    EntityType.TEACHER: (("employee_number",), ("phone",), ("email",)),
    EntityType.STUDENT: (("student_number",),),
    EntityType.MEMBERSHIP: (("member_mapping_id", "container_mapping_id", "role"),),
}

def non_null_key(values: dict, fields: tuple[str, ...]) -> tuple[str, ...] | None:
    parts = tuple(values.get(field) for field in fields)
    return parts if all(parts) else None
```

- [ ] **Step 4: Implement unique exact decision behavior and commit**

```python
class ExactMatcher:
    def match(self, source: NormalizedRecord, targets: Sequence[NormalizedRecord]) -> MatchDecision | None:
        for fields in STABLE_KEYS[source.entity_type]:
            key = non_null_key(source.values, fields)
            if not key:
                continue
            matches = [target for target in targets if non_null_key(target.values, fields) == key]
            if len(matches) == 1:
                return accepted_exact(source, matches[0], fields)
            if len(matches) > 1:
                return conflicting_exact(source, matches, fields)
        return None
```

Run: `cd backend && uv run pytest tests/unit/matching/test_exact_matcher.py -q`

Expected: unique stable IDs accept, duplicate keys conflict, and name-only cases remain unresolved.

```bash
git add backend/app/matching/exact_matcher.py backend/tests/unit/matching/test_exact_matcher.py
git commit -m "feat: add deterministic entity matching"
```

### Task 5: Build compatible blocks and lexical candidate retrieval

**Files:**
- Create: `backend/app/matching/blocking.py`
- Create: `backend/app/matching/candidate_retriever.py`
- Test: `backend/tests/unit/matching/test_candidate_retriever.py`

**Interfaces:**
- Consumes: unresolved normalized entity, matched-parent context, compatible target index, and `top_k`.
- Produces: bounded `Candidate(entity_id, lexical_score, vector_score, block_key)` lists.

- [ ] **Step 1: Test strict partitions and bounded retrieval**

```python
def test_teacher_candidates_stay_in_tenant_and_parent(retriever, source_teacher) -> None:
    candidates = retriever.lexical(source_teacher, top_k=5)
    assert len(candidates) <= 5
    assert all(c.block_key.tenant_id == source_teacher.tenant_id for c in candidates)
    assert all(c.block_key.parent_mapping_id == source_teacher.parent_mapping_id for c in candidates)

def test_no_compatible_block_returns_empty(retriever, source_teacher) -> None:
    assert retriever.lexical(source_teacher(tenant_id="unknown"), top_k=5) == []
```

- [ ] **Step 2: Run retrieval tests**

Run: `cd backend && uv run pytest tests/unit/matching/test_candidate_retriever.py -q`

Expected: FAIL because blocking and retrieval are absent.

- [ ] **Step 3: Define block keys per entity type**

```python
class BlockKey(BaseModel, frozen=True):
    tenant_id: str
    entity_type: EntityType
    campus_id: str | None = None
    grade: str | None = None
    parent_mapping_id: UUID | None = None

def block_key(record: NormalizedRecord) -> BlockKey:
    return BlockKey(
        tenant_id=record.tenant_id, entity_type=record.entity_type,
        campus_id=record.values.get("campus_id"),
        grade=record.values.get("grade") if record.entity_type is EntityType.CLASS else None,
        parent_mapping_id=record.parent_mapping_id,
    )
```

- [ ] **Step 4: Implement lexical Top-K with evidence**

```python
def lexical_score(source: NormalizedRecord, target: NormalizedRecord) -> float:
    name = fuzz.WRatio(source.values.get("display_name") or source.values.get("name") or "",
                       target.values.get("display_name") or target.values.get("name") or "") / 100
    path = fuzz.token_set_ratio(source.values.get("organization_path") or "",
                                target.values.get("organization_path") or "") / 100
    return 0.7 * name + 0.3 * path

def retrieve_lexical(source, targets, top_k: int) -> list[Candidate]:
    compatible = (t for t in targets if block_key(t) == block_key(source))
    ranked = sorted((Candidate.from_lexical(t, lexical_score(source, t)) for t in compatible),
                    key=lambda c: (-c.lexical_score, str(c.entity_id)))
    return ranked[:top_k]
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/unit/matching/test_candidate_retriever.py -q`

Expected: cross-tenant/context candidates are excluded and results never exceed Top-K.

```bash
git add backend/app/matching/blocking.py backend/app/matching/candidate_retriever.py backend/tests/unit/matching
git commit -m "feat: retrieve bounded lexical candidates"
```

### Task 6: Add embedding retrieval through pgvector

**Files:**
- Create: `backend/app/ai/providers/base.py`
- Create: `backend/app/matching/vector_index.py`
- Modify: `backend/app/models/mappings.py`
- Create: `backend/alembic/versions/0003_target_embeddings.py`
- Test: `backend/tests/integration/matching/test_vector_index.py`

**Interfaces:**
- Consumes: `EmbeddingProvider.embed(texts: Sequence[str]) -> list[list[float]]`, target representation text, model/version, and block key.
- Produces: cached target vectors and `VectorIndex.search(query, block, top_k)` candidates.

- [ ] **Step 1: Write restricted vector search test**

```python
async def test_vector_search_is_top_k_and_blocked(vector_index, fake_embeddings, teacher_block) -> None:
    await vector_index.upsert_targets(target_records(), fake_embeddings)
    results = await vector_index.search("张三 语文", teacher_block, top_k=3)
    assert len(results) <= 3
    assert all(item.block_key == teacher_block for item in results)
```

- [ ] **Step 2: Run pgvector test**

Run: `cd backend && uv run pytest tests/integration/matching/test_vector_index.py -q`

Expected: FAIL because vector index is missing.

- [ ] **Step 3: Define provider protocol and representation version**

```python
class EmbeddingBatch(BaseModel):
    vectors: list[list[float]]
    provider: str
    model: str
    usage_tokens: int = 0

class EmbeddingProvider(Protocol):
    dimensions: int
    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...

def representation(record: NormalizedRecord) -> str:
    fields = [record.entity_type.value, record.values.get("name"), record.values.get("organization_path"),
              record.values.get("grade"), record.values.get("subject_hint")]
    return " | ".join(str(value) for value in fields if value)
```

- [ ] **Step 4: Add vector storage and block-filtered cosine query**

Migration enables `vector`, creates `target_entity_embeddings(entity_id, model, representation_version, block_key JSONB, embedding vector(1536))`, and indexes embeddings with HNSW plus GIN on `block_key`.

```python
async def search(self, query: str, block: BlockKey, top_k: int) -> list[Candidate]:
    vector = (await self.provider.embed([query])).vectors[0]
    statement = select(TargetEmbedding).where(
        TargetEmbedding.block_key == block.model_dump(mode="json")
    ).order_by(TargetEmbedding.embedding.cosine_distance(vector)).limit(top_k)
    rows = (await self.session.scalars(statement)).all()
    return [Candidate.from_vector(row.entity_id, 1 - row.cosine_distance, block) for row in rows]
```

- [ ] **Step 5: Migrate, verify, and commit**

Run: `cd backend && uv run alembic upgrade head && uv run pytest tests/integration/matching/test_vector_index.py -q`

Expected: vector results are bounded, block-compatible, cached by model/version, and deterministic for the fake provider.

```bash
git add backend/app/ai/providers/base.py backend/app/matching/vector_index.py backend/app/models/mappings.py backend/alembic backend/tests/integration/matching
git commit -m "feat: add blocked vector candidate retrieval"
```

### Task 7: Score candidates and resolve one-to-one conflicts

**Files:**
- Create: `backend/app/matching/scorer.py`
- Create: `backend/app/matching/conflict_resolver.py`
- Test: `backend/tests/unit/matching/test_scorer.py`
- Test: `backend/tests/unit/matching/test_conflict_resolver.py`

**Interfaces:**
- Consumes: union of lexical/vector candidates and entity-specific feature values.
- Produces: scored evidence, accepted decision only when `score >= threshold` and `score1 - score2 >= margin`, otherwise review/conflict.

- [ ] **Step 1: Test thresholds, margin, and competition**

```python
def test_close_top_two_candidates_require_review(scorer, source, candidates) -> None:
    decision = scorer.decide(source, candidates_with_scores(0.91, 0.89))
    assert decision.status is MatchStatus.MANUAL_REVIEW

def test_competing_sources_do_not_share_target(resolver, decisions) -> None:
    resolved = resolver.resolve(two_sources_choose_same_target(decisions))
    assert {item.status for item in resolved} == {MatchStatus.CONFLICT}
```

- [ ] **Step 2: Run scorer tests**

Run: `cd backend && uv run pytest tests/unit/matching/test_scorer.py tests/unit/matching/test_conflict_resolver.py -q`

Expected: FAIL because scoring components do not exist.

- [ ] **Step 3: Implement versioned entity weights and decision rule**

```python
SCORE_POLICY_V1 = {
    EntityType.TEACHER: {"name": 0.35, "employee_number": 0.35, "phone": 0.15, "parent": 0.15},
    EntityType.STUDENT: {"name": 0.30, "student_number": 0.50, "class": 0.20},
    EntityType.CLASS: {"name": 0.35, "grade": 0.25, "school_year": 0.20, "parent": 0.20},
    EntityType.ORGANIZATION_UNIT: {"name": 0.55, "path": 0.25, "parent": 0.20},
    EntityType.MEMBERSHIP: {"member": 0.45, "container": 0.45, "role": 0.10},
}

def decision_status(first: float, second: float | None, threshold=.86, margin=.08) -> MatchStatus:
    if first < threshold or (second is not None and first - second < margin):
        return MatchStatus.MANUAL_REVIEW
    return MatchStatus.ACCEPTED
```

- [ ] **Step 4: Implement deterministic cardinality resolution**

```python
def resolve(decisions: Sequence[MatchDecision]) -> list[MatchDecision]:
    by_target: dict[UUID, list[MatchDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.target_entity_id:
            by_target[decision.target_entity_id].append(decision)
    conflicted = {d.source_entity_id for group in by_target.values() if len(group) > 1 for d in group}
    return [d.model_copy(update={"status": MatchStatus.CONFLICT}) if d.source_entity_id in conflicted else d
            for d in decisions]
```

- [ ] **Step 5: Verify evidence and commit**

Run: `cd backend && uv run pytest tests/unit/matching/test_scorer.py tests/unit/matching/test_conflict_resolver.py -q`

Expected: accepted, low-score, low-margin, and competing-mapping cases PASS with per-feature evidence.

```bash
git add backend/app/matching/scorer.py backend/app/matching/conflict_resolver.py backend/tests/unit/matching
git commit -m "feat: score and constrain entity mappings"
```

### Task 8: Orchestrate dependency-ordered resolution and prove scale

**Files:**
- Create: `backend/app/matching/service.py`
- Test: `backend/tests/integration/matching/test_resolution_service.py`
- Test: `backend/tests/performance/test_candidate_scale.py`
- Create: `backend/tests/fixtures/organization_factory.py`

**Interfaces:**
- Consumes: published snapshot pair, normalization pipeline, mapping repository, exact matcher, candidate retrievers, scorer, and conflict resolver.
- Produces: persisted `ResolutionSummary` and mapping decisions for module 3.

- [ ] **Step 1: Add dependency and scale acceptance tests**

```python
async def test_parent_mapping_becomes_teacher_evidence(service, snapshot_pair) -> None:
    summary = await service.resolve(snapshot_pair)
    teacher = next(d for d in summary.decisions if d.entity_type is EntityType.TEACHER)
    assert any(e.feature == "parent" and e.score == 1 for e in teacher.evidence)

async def test_candidate_calls_are_bounded(service, large_pair, spy_retriever) -> None:
    await service.resolve(large_pair)
    assert spy_retriever.max_returned <= 20
    assert spy_retriever.comparisons < large_pair.source_count * large_pair.target_count
```

- [ ] **Step 2: Run integration tests**

Run: `cd backend && uv run pytest tests/integration/matching/test_resolution_service.py tests/performance/test_candidate_scale.py -q`

Expected: FAIL because the service is absent.

- [ ] **Step 3: Implement ordered orchestration**

```python
RESOLUTION_ORDER = (
    EntityType.ORGANIZATION_UNIT, EntityType.CLASS, EntityType.TEACHER,
    EntityType.STUDENT, EntityType.MEMBERSHIP,
)

class EntityResolutionService:
    async def resolve(self, pair: SnapshotPair) -> ResolutionSummary:
        decisions: list[MatchDecision] = []
        for entity_type in RESOLUTION_ORDER:
            context = await self.mappings.parent_context(pair.task_id, entity_type)
            stage = await self._resolve_type(pair, entity_type, context)
            await self.mappings.save_stage(pair.task_id, entity_type, stage)
            decisions.extend(stage)
        return ResolutionSummary.from_decisions(decisions)
```

- [ ] **Step 4: Add representative accuracy fixtures**

Generate synthetic cases for exact employee/student IDs, missing identifiers, `高一(1)班` vs `2024级1班`, `张三（语文）` vs `张三`, confirmed mapping reuse, revoked mappings, hierarchy evidence, duplicate keys, close candidates, and cross-tenant exclusions.

Run: `cd backend && uv run pytest tests/unit/normalization tests/unit/matching tests/integration/matching tests/performance/test_candidate_scale.py -q`

Expected: all resolution tests PASS; no source performs an unrestricted all-target comparison.

- [ ] **Step 5: Commit the completed resolver**

```bash
git add backend/app/matching/service.py backend/tests/integration/matching backend/tests/performance backend/tests/fixtures
git commit -m "feat: orchestrate scalable entity resolution"
```

## Module Acceptance

Run: `cd backend && uv run pytest tests/unit/normalization tests/unit/matching tests/integration/matching tests/performance/test_candidate_scale.py -q && uv run ruff check . && uv run mypy app`

Expected: all five entity types resolve in dependency order; exact and historical decisions are explainable; candidate lists are bounded; uncertain and competing pairs remain non-accepted; every persisted decision carries rule and evidence provenance.

# Reconciliation Differences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert immutable snapshot pairs and entity-resolution results into queryable, evidence-rich Seewo missing, Seewo redundant, attribute, structure, and duplicate differences.

**Architecture:** A deterministic comparison service consumes published canonical entities and persisted match decisions. Versioned field policies decide which fields are governed and how nulls, sets, and normalized values compare; the classifier emits append-only difference records bound to both snapshots, the match, exact fields, and policy version. REST endpoints expose stable cursor pagination and detail evidence without recalculating historical results.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL JSONB, pytest.

## Global Constraints

- Third-party data is authoritative; proposed corrections always point toward the Seewo target.
- Difference classification is deterministic; LLM cause analysis happens only in module 4.
- A `PARTIAL` snapshot never produces Seewo-redundant differences.
- `UNMATCHED` source means Seewo missing; `MANUAL_REVIEW` waits for confirmation, and `CONFLICT` becomes duplicate conflict.
- Unmatched target means Seewo redundant only for full matching scope; targets reserved by manual-review or conflict mappings are not considered redundant.
- Redundant entities are never translated directly into delete operations.
- Structural fields (parent, class, department, membership container) are distinct from ordinary attributes.
- Every difference is immutable and bound to source snapshot, target snapshot, mapping, field policy, and comparison version.
- API pagination order is `(created_at DESC, id DESC)` with opaque cursors.

---

## File Map

- `backend/app/schemas/differences.py`: difference types, evidence, list/detail responses, filters.
- `backend/app/models/differences.py`: append-only difference records and indexes.
- `backend/app/differences/`: comparison policies, classifier, detector, and service.
- `backend/app/repositories/differences.py`: immutable bulk insert and stable queries.
- `backend/app/api/routes/differences.py`: task-scoped list/detail API.

### Task 1: Define difference contracts and persistence

**Files:**
- Create: `backend/app/schemas/differences.py`
- Create: `backend/app/models/differences.py`
- Create: `backend/app/repositories/differences.py`
- Create: `backend/alembic/versions/0004_differences.py`
- Test: `backend/tests/integration/repositories/test_differences.py`

**Interfaces:**
- Consumes: task ID, snapshot IDs, optional mapping ID, compared fields, raw references, and proposed action.
- Produces: immutable `DifferenceItem`, `DifferenceEvidence`, `DifferenceRepository.insert_many`, `list`, and `get`.

- [ ] **Step 1: Write immutability and snapshot-binding tests**

```python
async def test_difference_requires_both_snapshot_ids(repo, difference_factory) -> None:
    item = difference_factory(target_snapshot_id=None)
    with pytest.raises(ValueError, match="target_snapshot_id"):
        await repo.insert_many([item])

async def test_difference_cannot_be_updated(repo, saved_difference) -> None:
    with pytest.raises(ImmutableRecordError):
        await repo.replace_evidence(saved_difference.id, {"field": "changed"})
```

- [ ] **Step 2: Run repository tests**

Run: `cd backend && uv run pytest tests/integration/repositories/test_differences.py -q`

Expected: FAIL because difference contracts and repository are missing.

- [ ] **Step 3: Define exact schemas**

```python
class DifferenceType(StrEnum):
    SEEWO_MISSING = "seewo_missing"
    SEEWO_REDUNDANT = "seewo_redundant"
    ATTRIBUTE_CONFLICT = "attribute_conflict"
    STRUCTURE_CONFLICT = "structure_conflict"
    DUPLICATE_CONFLICT = "duplicate_conflict"

class DifferenceStatus(StrEnum):
    OPEN = "open"
    SELECTED = "selected"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"

class FieldDifference(BaseModel):
    field: str
    source_value: Any
    target_value: Any
    normalized_source: Any
    normalized_target: Any
    comparison: str

class DifferenceEvidence(BaseModel):
    source_snapshot_id: UUID
    target_snapshot_id: UUID
    source_entity_id: UUID | None
    target_entity_id: UUID | None
    mapping_id: UUID | None
    fields: tuple[FieldDifference, ...]
    match_evidence: tuple[MatchEvidence, ...] = ()
    raw_source_row: int | None = None
    raw_target_row: int | None = None
    comparison_rule_version: str

class DifferenceItem(BaseModel):
    id: UUID
    task_id: UUID
    entity_type: EntityType
    difference_type: DifferenceType
    status: DifferenceStatus = DifferenceStatus.OPEN
    proposed_action: str
    evidence: DifferenceEvidence
    version: int = 1
```

- [ ] **Step 4: Add append-only table and query indexes**

```python
class DifferenceRecord(Base, TimestampMixin):
    __tablename__ = "difference_items"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    source_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"))
    target_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"))
    mapping_id: Mapped[UUID | None] = mapped_column(ForeignKey("entity_mappings.id"))
    entity_type: Mapped[str] = mapped_column(index=True)
    difference_type: Mapped[str] = mapped_column(index=True)
    resolution_status: Mapped[str] = mapped_column(index=True, default="open")
    evidence: Mapped[dict] = mapped_column(JSONB)
    proposed_action: Mapped[str]
    comparison_rule_version: Mapped[str]
    version: Mapped[int] = mapped_column(default=1)
```

Migration adds composite indexes for `(task_id, entity_type, difference_type, resolution_status, created_at, id)` and a uniqueness key on `(task_id, source_snapshot_id, target_snapshot_id, entity_type, evidence_hash)`.

- [ ] **Step 5: Migrate, verify, and commit**

Run: `cd backend && uv run alembic upgrade head && uv run pytest tests/integration/repositories/test_differences.py -q`

Expected: snapshot constraints, immutable evidence, and duplicate insertion idempotency tests PASS.

```bash
git add backend/app/schemas/differences.py backend/app/models/differences.py backend/app/repositories/differences.py backend/alembic backend/tests/integration/repositories/test_differences.py
git commit -m "feat: persist snapshot-bound differences"
```

### Task 2: Implement versioned field comparison policies

**Files:**
- Create: `backend/app/differences/field_policies.py`
- Create: `backend/app/differences/policies.v1.json`
- Test: `backend/tests/unit/differences/test_field_policies.py`

**Interfaces:**
- Consumes: entity type plus normalized source and target payloads.
- Produces: ordered `FieldDifference` values and policy classification (`attribute` or `structure`).

- [ ] **Step 1: Write policy behavior tests**

```python
def test_phone_formatting_is_equivalent(policy) -> None:
    assert policy.compare(EntityType.TEACHER, {"phone": "13800000000"}, {"phone": "+86 138-0000-0000"}) == ()

def test_department_change_is_structural(policy) -> None:
    fields = policy.compare(EntityType.TEACHER, {"department_mapping_id": "d1"}, {"department_mapping_id": "d2"})
    assert fields[0].comparison == "structure"

def test_ungoverned_raw_field_is_ignored(policy) -> None:
    assert policy.compare(EntityType.STUDENT, {"import_note": "a"}, {"import_note": "b"}) == ()
```

- [ ] **Step 2: Run policy tests**

Run: `cd backend && uv run pytest tests/unit/differences/test_field_policies.py -q`

Expected: FAIL because policies do not exist.

- [ ] **Step 3: Define explicit governed fields**

```python
class CompareKind(StrEnum):
    NORMALIZED_SCALAR = "normalized_scalar"
    UNORDERED_SET = "unordered_set"
    STRUCTURE_ID = "structure_id"

class FieldRule(BaseModel):
    field: str
    kind: CompareKind

class EntityPolicy(BaseModel):
    entity_type: EntityType
    rules: tuple[FieldRule, ...]

POLICY_V1 = {
    EntityType.ORGANIZATION_UNIT: (("name", "normalized_scalar"), ("parent_mapping_id", "structure_id")),
    EntityType.CLASS: (("name", "normalized_scalar"), ("grade", "normalized_scalar"), ("parent_mapping_id", "structure_id")),
    EntityType.TEACHER: (("name", "normalized_scalar"), ("phone", "normalized_scalar"), ("email", "normalized_scalar"), ("department_mapping_id", "structure_id")),
    EntityType.STUDENT: (("name", "normalized_scalar"), ("class_mapping_id", "structure_id")),
    EntityType.MEMBERSHIP: (("container_mapping_id", "structure_id"), ("role", "normalized_scalar")),
}
```

- [ ] **Step 4: Implement deterministic comparison**

```python
def equivalent(rule: FieldRule, source: Any, target: Any) -> bool:
    if rule.kind is CompareKind.UNORDERED_SET:
        return set(source or ()) == set(target or ())
    return source == target

def compare(entity_type, source, target) -> tuple[FieldDifference, ...]:
    differences = []
    for field, kind in POLICY_V1[entity_type]:
        rule = FieldRule(field=field, kind=kind)
        if not equivalent(rule, source.get(field), target.get(field)):
            differences.append(FieldDifference(
                field=field, source_value=source.get(field), target_value=target.get(field),
                normalized_source=source.get(field), normalized_target=target.get(field),
                comparison="structure" if kind == "structure_id" else "attribute"))
    return tuple(differences)
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/unit/differences/test_field_policies.py -q`

Expected: null, normalized scalar, set, governed/ungoverned, and structural tests PASS.

```bash
git add backend/app/differences backend/tests/unit/differences
git commit -m "feat: add versioned field comparison policies"
```

### Task 3: Classify missing, redundant, conflicts, and duplicates

**Files:**
- Create: `backend/app/differences/classifier.py`
- Test: `backend/tests/unit/differences/test_classifier.py`

**Interfaces:**
- Consumes: source/target entities, match status, scope mode, field differences, duplicate groups.
- Produces: zero or more `DifferenceDraft` records with safe proposed actions.

- [ ] **Step 1: Write one test per required classification**

```python
def test_unmatched_source_is_seewo_missing(classifier, source_teacher) -> None:
    item = classifier.unmatched_source(source_teacher)
    assert item.difference_type is DifferenceType.SEEWO_MISSING
    assert item.proposed_action == "create"

def test_partial_scope_suppresses_redundant(classifier, target_teacher) -> None:
    assert classifier.unmatched_target(target_teacher, SnapshotMode.PARTIAL) is None

def test_structural_field_wins_over_attribute(classifier, matched_pair) -> None:
    item = classifier.matched(matched_pair, fields=(structure_diff(), attribute_diff()))
    assert item.difference_type is DifferenceType.STRUCTURE_CONFLICT

def test_duplicate_key_is_explicit_conflict(classifier, duplicate_group) -> None:
    assert classifier.duplicates(duplicate_group).difference_type is DifferenceType.DUPLICATE_CONFLICT
```

- [ ] **Step 2: Run classifier tests**

Run: `cd backend && uv run pytest tests/unit/differences/test_classifier.py -q`

Expected: FAIL because classifier is missing.

- [ ] **Step 3: Implement deterministic classification priority**

```python
def classify_matched(fields: tuple[FieldDifference, ...]) -> DifferenceType | None:
    if not fields:
        return None
    if any(field.comparison == "structure" for field in fields):
        return DifferenceType.STRUCTURE_CONFLICT
    return DifferenceType.ATTRIBUTE_CONFLICT

def unmatched_target(entity, mode: SnapshotMode) -> DifferenceDraft | None:
    if mode is not SnapshotMode.FULL:
        return None
    return DifferenceDraft.from_target(entity, DifferenceType.SEEWO_REDUNDANT, proposed_action="disable")
```

- [ ] **Step 4: Ensure delete is never proposed and commit**

Run: `cd backend && uv run pytest tests/unit/differences/test_classifier.py -q`

Expected: all five classifications PASS and no draft has proposed action `delete`.

```bash
git add backend/app/differences/classifier.py backend/tests/unit/differences/test_classifier.py
git commit -m "feat: classify reconciliation differences"
```

### Task 4: Detect and persist differences as an idempotent stage

**Files:**
- Create: `backend/app/differences/detector.py`
- Create: `backend/app/differences/service.py`
- Test: `backend/tests/integration/differences/test_detection_service.py`

**Interfaces:**
- Consumes: `SnapshotPair`, all resolution decisions, canonical repositories, policy version.
- Produces: persisted `DifferenceSummary` with counts by entity/difference type and task stage transition `MATCHED -> DIFFERENCES_READY`.

- [ ] **Step 1: Write end-to-end detection tests**

```python
async def test_detection_binds_exact_evidence(service, resolved_pair) -> None:
    summary = await service.detect(resolved_pair.task_id)
    item = await service.repository.get(summary.difference_ids[0])
    assert item.evidence.source_snapshot_id == resolved_pair.authoritative_id
    assert item.evidence.target_snapshot_id == resolved_pair.target_id
    assert item.evidence.comparison_rule_version == "comparison-v1"

async def test_retry_does_not_duplicate_differences(service, resolved_pair) -> None:
    first = await service.detect(resolved_pair.task_id)
    second = await service.detect(resolved_pair.task_id)
    assert second.difference_ids == first.difference_ids
```

- [ ] **Step 2: Run service tests**

Run: `cd backend && uv run pytest tests/integration/differences/test_detection_service.py -q`

Expected: FAIL because detection orchestration is absent.

- [ ] **Step 3: Implement complete-set accounting**

```python
class DifferenceDetector:
    def detect(self, source, target, decisions, scope) -> list[DifferenceDraft]:
        by_source = {decision.source_entity_id: decision for decision in decisions}
        consumed_targets = {decision.target_entity_id for decision in decisions if decision.status is MatchStatus.ACCEPTED}
        drafts = [self.classifier.unmatched_source(entity) for entity in source if by_source.get(entity.id, None) is None]
        drafts += [item for entity in target
                   if entity.id not in consumed_targets
                   if (item := self.classifier.unmatched_target(entity, scope.mode)) is not None]
        drafts += self._matched_conflicts(source, target, decisions)
        drafts += self._duplicate_conflicts(source, target)
        return drafts
```

- [ ] **Step 4: Publish the stage transactionally**

```python
async def detect(self, task_id: UUID) -> DifferenceSummary:
    existing = await self.repository.summary_for_task(task_id)
    if existing and existing.stage_complete:
        return existing
    context = await self.context.load_resolved_task(task_id)
    drafts = self.detector.detect(context.source, context.target, context.decisions, context.scope)
    async with self.session.begin():
        items = await self.repository.insert_many(drafts)
        await self.tasks.transition(task_id, expected="matching", target="differences_ready")
    return DifferenceSummary.from_items(items)
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/integration/differences/test_detection_service.py -q`

Expected: classification counts, evidence binding, task transition, and retry idempotency tests PASS.

```bash
git add backend/app/differences backend/tests/integration/differences
git commit -m "feat: detect snapshot-bound differences"
```

### Task 5: Expose stable difference list and detail APIs

**Files:**
- Create: `backend/app/api/routes/differences.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/api/test_differences.py`

**Interfaces:**
- Consumes: task, entity type, difference type, analysis status, risk, resolution status, cursor, and limit filters.
- Produces: `GET /api/reconciliation-tasks/{task_id}/differences` and `GET /api/differences/{difference_id}`.

- [ ] **Step 1: Write filter, cursor, and detail tests**

```python
def test_list_filters_and_has_stable_cursor(client, seeded_differences) -> None:
    response = client.get(f"/api/reconciliation-tasks/{seeded_differences.task_id}/differences",
                          params={"entity_type": "teacher", "difference_type": "attribute_conflict", "limit": 2})
    body = response.json()
    assert len(body["items"]) <= 2
    assert body["next_cursor"]
    assert all(i["entity_type"] == "teacher" for i in body["items"])

def test_detail_returns_raw_and_match_evidence(client, seeded_difference) -> None:
    body = client.get(f"/api/differences/{seeded_difference.id}").json()
    assert body["evidence"]["fields"]
    assert "match_evidence" in body["evidence"]
```

- [ ] **Step 2: Run API tests**

Run: `cd backend && uv run pytest tests/integration/api/test_differences.py -q`

Expected: FAIL with 404 because routes are absent.

- [ ] **Step 3: Implement typed filters and routes**

```python
class DifferenceFilters(BaseModel):
    entity_type: EntityType | None = None
    difference_type: DifferenceType | None = None
    analysis_status: str | None = None
    risk: str | None = None
    resolution_status: DifferenceStatus | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)

@router.get("/reconciliation-tasks/{task_id}/differences", response_model=DifferencePage)
async def list_differences(task_id: UUID, filters: Annotated[DifferenceFilters, Query()], repo=Depends(get_difference_repo)):
    return await repo.list(task_id, filters)

@router.get("/differences/{difference_id}", response_model=DifferenceDetail)
async def get_difference(difference_id: UUID, repo=Depends(get_difference_repo)):
    return await repo.get_or_404(difference_id)
```

- [ ] **Step 4: Verify filters and commit**

Run: `cd backend && uv run pytest tests/integration/api/test_differences.py -q`

Expected: task isolation, all filters, cursor stability during concurrent insertion, limit bounds, 404, and detail evidence tests PASS.

```bash
git add backend/app/api/routes/differences.py backend/app/main.py backend/tests/integration/api/test_differences.py
git commit -m "feat: expose paginated difference evidence"
```

### Task 6: Prove classification accuracy and non-all-pairs behavior

**Files:**
- Create: `backend/tests/fixtures/difference_cases.json`
- Create: `backend/tests/integration/differences/test_accuracy_matrix.py`
- Create: `backend/tests/performance/test_difference_scale.py`

**Interfaces:**
- Consumes: synthetic full and partial snapshot pairs from modules 1 and 2.
- Produces: regression metrics and query-count assertions for the complete deterministic checking pipeline.

- [ ] **Step 1: Add an explicit accuracy matrix**

```python
@pytest.mark.parametrize("case", load_difference_cases())
async def test_expected_difference(case, full_pipeline) -> None:
    result = await full_pipeline.run(case.snapshot_pair)
    assert [(item.entity_type.value, item.difference_type.value) for item in result.differences] == case.expected
```

The JSON cases must cover all five entity types, all five difference types, equal normalized values, full/partial scope, duplicate IDs, unmatched targets, mixed attribute/structure changes, and no-difference pairs.

- [ ] **Step 2: Add scale instrumentation**

```python
async def test_ten_thousand_entities_do_not_trigger_all_pairs(large_pipeline, query_spy) -> None:
    result = await large_pipeline.run(entity_count=10_000)
    assert result.processed == 20_000
    assert result.max_candidates_per_source <= 20
    assert query_spy.candidate_rows_read < 10_000 * 20
```

- [ ] **Step 3: Run the complete module suite**

Run: `cd backend && uv run pytest tests/unit/differences tests/integration/differences tests/integration/api/test_differences.py tests/performance/test_difference_scale.py -q`

Expected: all accuracy cases PASS and scale assertions prove bounded retrieval rather than Cartesian comparison.

- [ ] **Step 4: Commit acceptance coverage**

```bash
git add backend/tests/fixtures/difference_cases.json backend/tests/integration/differences/test_accuracy_matrix.py backend/tests/performance/test_difference_scale.py
git commit -m "test: verify difference accuracy and scale"
```

## Module Acceptance

Run: `cd backend && uv run pytest tests/unit/differences tests/integration/differences tests/integration/api/test_differences.py tests/performance/test_difference_scale.py -q && uv run ruff check . && uv run mypy app`

Expected: deterministic differences are complete for the selected scope, partial snapshots cannot create false redundant records, every item exposes exact evidence, pagination remains stable, and no LLM call is made.

# Reconciliation Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four reviewed ingestion, matching, and workflow defects without changing the uncommitted AI-analysis work in the main worktree.

**Architecture:** Keep the modular service boundaries already present. Validate mapping profiles before CSV columns, broaden the deterministic cardinality resolver, reuse complete persisted resolution results under a task row lock, and expose entity resolution through the existing reconciliation-task router.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async, pytest, Ruff, mypy, React/Vite verification, OpenSpec.

## Global Constraints

- Work only in `fix/reconciliation-review-issues` at `.worktrees/fix-reconciliation-review-issues`.
- Do not copy, stage, modify, or revert the main worktree's uncommitted AI-analysis files.
- Use test-first red-green-refactor for every production behavior change.
- Preserve append-only manual mapping decisions and historical-mapping priority.
- Do not add Celery, SSE, automatic AI analysis, or model-provider wiring.
- Use `POST /api/reconciliation-tasks/{task_id}/resolve` as the explicit matching entry point.
- A retry for a complete task/snapshot decision set must not insert mapping or difference records.

---

## File map

- `backend/app/ingestion/field_mapping.py`: declares required canonical mapping keys safely.
- `backend/app/ingestion/schema_validation.py`: emits fatal missing-mapping issues before CSV-column checks.
- `backend/app/matching/conflict_resolver.py`: enforces cardinality for accepted, review, and conflict decisions.
- `backend/app/matching/service.py`: locks the task and reuses complete persisted decisions.
- `backend/app/api/routes/reconciliation_tasks.py`: exposes task-scoped entity resolution.
- `backend/tests/unit/ingestion/test_csv_ingestion.py`: mapping configuration regression coverage.
- `backend/tests/unit/matching/test_conflict_resolver.py`: target competition regression coverage.
- `backend/tests/integration/matching/test_resolution_service.py`: retry idempotency coverage.
- `backend/tests/integration/api/test_differences.py`: API workflow coverage through differences.

### Task 1: Report incomplete mapping profiles

**Files:**
- Modify: `backend/app/ingestion/field_mapping.py`
- Modify: `backend/app/ingestion/schema_validation.py`
- Test: `backend/tests/unit/ingestion/test_csv_ingestion.py`

**Interfaces:**
- Consumes: `FieldMappingProfile.columns: dict[str, str]`.
- Produces: `FieldMappingProfile.missing_required_mappings() -> tuple[str, ...]` and fatal `IngestionIssue(code="missing_required_mapping")` values.

- [ ] **Step 1: Write the failing profile test**

Add a test that removes `name` from a copied default profile and calls `validate_frame`:

```python
def test_missing_required_profile_mapping_is_fatal(tmp_path: Path) -> None:
    base = default_mapping_registry().get("mofa-v1")
    columns = dict(base.columns)
    columns.pop("name")
    profile = FieldMappingProfile(
        version="broken-v1",
        name="broken",
        source_role=SourceRole.TARGET,
        columns=columns,
        entity_type_values=base.entity_type_values,
    )
    frame = pl.DataFrame({"entity_type": ["教师"], "id": ["T1"], "name": ["A"]})

    result = validate_frame(
        frame,
        profile=profile,
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=SourceRole.TARGET,
    )

    assert result.summary.accepted == 0
    assert result.fatal_errors[0].code == "missing_required_mapping"
    assert result.fatal_errors[0].field == "name"
```

- [ ] **Step 2: Run the test and verify red**

Run: `cd backend && .venv/bin/pytest tests/unit/ingestion/test_csv_ingestion.py::test_missing_required_profile_mapping_is_fatal -q`

Expected: FAIL with `KeyError: 'name'`.

- [ ] **Step 3: Implement mapping-first validation**

Define required keys without indexing missing dictionary entries:

```python
REQUIRED_CANONICAL_MAPPINGS = (
    "entity_type",
    "source_id",
    "name",
    "member_source_id",
    "container_source_id",
    "role",
)

def missing_required_mappings(self) -> tuple[str, ...]:
    return tuple(key for key in REQUIRED_CANONICAL_MAPPINGS if not self.columns.get(key))
```

At the start of `validate_frame`, return one fatal `IngestionIssue` per missing key before accessing `required_source_columns`. Keep the existing `missing_required_column` behavior for configured columns absent from the frame.

- [ ] **Step 4: Verify green and regression coverage**

Run: `cd backend && .venv/bin/pytest tests/unit/ingestion -q`

Expected: all ingestion unit tests PASS, including distinct missing-mapping and missing-column cases.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/field_mapping.py backend/app/ingestion/schema_validation.py backend/tests/unit/ingestion/test_csv_ingestion.py
git commit -m "fix: validate required field mappings"
```

### Task 2: Treat review target competition as duplicate conflict

**Files:**
- Modify: `backend/app/matching/conflict_resolver.py`
- Test: `backend/tests/unit/matching/test_conflict_resolver.py`

**Interfaces:**
- Consumes: `Sequence[MatchDecision]` containing target-bearing accepted, manual-review, or conflict decisions.
- Produces: conflict decisions for every competing source except one uniquely protected historical mapping.

- [ ] **Step 1: Write failing competition tests**

Extend the test helper to accept a status, then add:

```python
def test_manual_review_sources_competing_for_target_become_conflicts() -> None:
    target_id = uuid4()
    decisions = [
        decision(uuid4(), target_id, status=MatchStatus.MANUAL_REVIEW),
        decision(uuid4(), target_id, status=MatchStatus.MANUAL_REVIEW),
    ]
    assert {item.status for item in ConflictResolver().resolve(decisions)} == {
        MatchStatus.CONFLICT
    }

def test_later_decision_cannot_consume_an_existing_conflict_target() -> None:
    target_id = uuid4()
    existing = decision(uuid4(), target_id, status=MatchStatus.CONFLICT)
    incoming = decision(uuid4(), target_id)
    resolved = ConflictResolver().resolve([existing, incoming])
    assert {item.status for item in resolved} == {MatchStatus.CONFLICT}
```

- [ ] **Step 2: Run the tests and verify red**

Run: `cd backend && .venv/bin/pytest tests/unit/matching/test_conflict_resolver.py -q`

Expected: the two new tests FAIL because only accepted decisions are grouped.

- [ ] **Step 3: Broaden target grouping**

Change grouping to include target-bearing decisions in these states:

```python
CARDINALITY_STATUSES = {
    MatchStatus.ACCEPTED,
    MatchStatus.MANUAL_REVIEW,
    MatchStatus.CONFLICT,
}

if decision.status in CARDINALITY_STATUSES and decision.target_entity_id is not None:
    groups[decision.target_entity_id].append(decision)
```

Keep the existing unique historical winner behavior and `_as_conflict` evidence.

- [ ] **Step 4: Verify green and matching regressions**

Run: `cd backend && .venv/bin/pytest tests/unit/matching tests/integration/differences/test_accuracy_matrix.py -q`

Expected: all matching unit and difference accuracy tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/matching/conflict_resolver.py backend/tests/unit/matching/test_conflict_resolver.py
git commit -m "fix: detect review mapping competition"
```

### Task 3: Make entity resolution retries idempotent

**Files:**
- Modify: `backend/app/matching/service.py`
- Test: `backend/tests/integration/matching/test_resolution_service.py`
- Test: `backend/tests/integration/differences/test_detection_service.py`

**Interfaces:**
- Consumes: `SnapshotPair` and persisted `EntityMapping` rows for the exact task and snapshot pair.
- Produces: a stable `ResolutionSummary` without inserting rows when the latest decision set covers every source entity.

- [ ] **Step 1: Write the failing resolution retry test**

```python
@pytest.mark.asyncio
async def test_resolution_retry_reuses_complete_decision_set(session) -> None:
    pair = await create_hierarchy_pair(session)
    service = EntityResolutionService(session)
    first = await service.resolve(pair)
    first_count = await session.scalar(select(func.count()).select_from(EntityMapping))

    second = await service.resolve(pair)
    second_count = await session.scalar(select(func.count()).select_from(EntityMapping))

    assert second_count == first_count
    assert second.decisions == first.decisions
```

Add an end-to-end retry test that performs resolve/detect twice and asserts the difference IDs do not grow.

- [ ] **Step 2: Run the tests and verify red**

Run: `cd backend && .venv/bin/pytest tests/integration/matching/test_resolution_service.py::test_resolution_retry_reuses_complete_decision_set tests/integration/differences/test_detection_service.py::test_resolution_and_detection_retry_do_not_append_differences -q`

Expected: FAIL because mapping rows and mapping-bound difference evidence are duplicated.

- [ ] **Step 3: Lock and load the latest persisted decisions**

Acquire the task through `TaskRepository.get_for_update` before stage inspection. Query mappings for the exact task/source/target snapshots ordered by creation and ID, retain the latest row per source entity, and convert rows back to `MatchDecision` values:

```python
def _decision_from_record(record: EntityMapping) -> MatchDecision:
    return MatchDecision(
        entity_type=EntityType(record.entity_type),
        source_entity_id=record.source_entity_id,
        source_key=record.source_key,
        target_entity_id=record.target_entity_id,
        target_key=record.target_key,
        method=MatchMethod(record.method) if record.method else None,
        status=MatchStatus(record.status),
        confidence=float(record.confidence),
        evidence=tuple(MatchEvidence.model_validate(item) for item in record.evidence),
        rule_version=record.rule_version,
        confirmed_by=record.confirmed_by,
    )
```

When the latest decision keys equal the authoritative entity IDs, return a summary from those decisions. Otherwise run the existing resolver. Do not add a uniqueness constraint.

- [ ] **Step 4: Verify green and downstream idempotency**

Run: `cd backend && .venv/bin/pytest tests/integration/matching tests/integration/differences -q`

Expected: all tests PASS; repeated resolution retains the same mapping and difference counts.

- [ ] **Step 5: Commit**

```bash
git add backend/app/matching/service.py backend/tests/integration/matching/test_resolution_service.py backend/tests/integration/differences/test_detection_service.py
git commit -m "fix: reuse completed entity resolution"
```

### Task 4: Expose entity resolution through the task API

**Files:**
- Modify: `backend/app/matching/service.py`
- Modify: `backend/app/api/routes/reconciliation_tasks.py`
- Test: `backend/tests/integration/api/test_differences.py`

**Interfaces:**
- Consumes: task ID with one published authoritative and one published target snapshot.
- Produces: `EntityResolutionService.resolve_task(task_id: UUID) -> ResolutionSummary` and `POST /api/reconciliation-tasks/{task_id}/resolve`.

- [ ] **Step 1: Write the failing API workflow test**

Seed a snapshot-ready task through `create_hierarchy_pair`, call the endpoint, then call difference detection:

```python
def test_task_can_resolve_then_detect_differences(difference_client: TestClient) -> None:
    task_id, _pair = seed_snapshot_pair(difference_client)

    resolution = difference_client.post(f"/api/reconciliation-tasks/{task_id}/resolve")
    detection = difference_client.post(
        f"/api/reconciliation-tasks/{task_id}/differences/detect"
    )

    assert resolution.status_code == 200
    assert resolution.json()["task_id"] == str(task_id)
    assert resolution.json()["processed_entity_types"]
    assert detection.status_code == 200
```

Add a `404` test for an unknown task ID.

- [ ] **Step 2: Run the API tests and verify red**

Run: `cd backend && .venv/bin/pytest tests/integration/api/test_differences.py -q`

Expected: FAIL because the resolve route returns `404`.

- [ ] **Step 3: Add task-scoped resolution orchestration**

Implement `resolve_task` by loading the task and its two published snapshots with existing repositories, building `SnapshotPair`, and delegating to `resolve`:

```python
async def resolve_task(self, task_id: UUID) -> ResolutionSummary:
    task = await self.session.get(ReconciliationTask, task_id)
    if task is None:
        raise LookupError(f"reconciliation task not found: {task_id}")
    source = await self.snapshots.get_for_task_role(task_id, SourceRole.AUTHORITATIVE)
    target = await self.snapshots.get_for_task_role(task_id, SourceRole.TARGET)
    if source is None or target is None:
        raise ValueError("entity resolution requires a published snapshot pair")
    return await self.resolve(SnapshotPair(
        task_id=task_id,
        tenant_id=task.tenant_id,
        source_snapshot_id=source.id,
        target_snapshot_id=target.id,
    ))
```

Register the POST route with `ResolutionSummary`, mapping `LookupError` to `404` and `ValueError` to `409`.

- [ ] **Step 4: Verify green and API regressions**

Run: `cd backend && .venv/bin/pytest tests/integration/api/test_ingestion_api.py tests/integration/api/test_differences.py -q`

Expected: task creation, resolution, detection, filters, and error paths PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/matching/service.py backend/app/api/routes/reconciliation_tasks.py backend/tests/integration/api/test_differences.py
git commit -m "feat: expose task entity resolution"
```

### Task 5: Verify and integrate the branch

**Files:**
- Verify only; do not edit unrelated AI-analysis files.

**Interfaces:**
- Consumes: completed fix branch.
- Produces: clean verification evidence and a merge commit on `master`.

- [ ] **Step 1: Run backend verification**

Run: `cd backend && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/mypy app`

Expected: all backend tests PASS; Ruff and mypy report no issues.

- [ ] **Step 2: Run frontend and OpenSpec verification**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck && npm run build`

Run: `openspec validate demo`

Expected: frontend tests, lint, typecheck, build, and OpenSpec validation PASS.

- [ ] **Step 3: Inspect branch scope**

Run: `git status --short && git diff --check master...HEAD && git diff --stat master...HEAD`

Expected: clean branch; only the design, plan, four fixes, and their tests differ from `master`.

- [ ] **Step 4: Merge without disturbing main-worktree changes**

In the main worktree, confirm none of the uncommitted paths overlap the branch diff. Then merge with:

```bash
git merge --no-ff fix/reconciliation-review-issues -m "merge: fix reconciliation review issues"
```

Expected: merge succeeds without staging or changing the unrelated AI-analysis working-tree files.

- [ ] **Step 5: Verify merged commit and preserve user state**

Run the committed-code verification available in the main worktree and compare `git status --short` with the pre-merge list.

Expected: the same uncommitted AI paths remain; no additional unstaged changes are introduced by the merge.

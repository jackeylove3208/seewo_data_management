# Stable CSV locators and report status badges implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep CSV target identifiers stable across synchronization rounds and expose approval, input-anomaly, and execution-failure states independently in Agent reports.

**Architecture:** The CSV ingestion adapter will adopt the persisted target `id` as the target record locator while retaining physical-row fallback for first-time files. Governance plan compilation will reject a locator that resolves to a different business identity. The report page will render approval and execution as separate tags and derive partial completion from mutation outcomes.

**Tech Stack:** Python 3.12, FastAPI domain services, Pydantic, pytest, React, TypeScript, Ant Design, Vitest, Testing Library, CSS.

## Global constraints

- Apply this change only to CSV-backed Agent synchronization and the Agent report page.
- Do not change manual synchronization, SQL connector identifiers, approval policy, rollback policy, or business matching rules.
- Authoritative CSV input remains read-only and keeps physical-row locators.
- Target CSV input uses a non-empty persisted `id`; a file without `id` falls back to `csv:<physical-row>`.
- Duplicate or empty persisted target IDs fail closed.
- Approval and execution outcomes remain independent report facts.
- Preserve unrelated user changes in `frontend/tests/e2e/agent-workflow.spec.ts` and `frontend/tests/e2e/reconciliation-flow.spec.ts`.

---

### Task 1: Persist target CSV locators across synchronization rounds

**Files:**

- Modify: `backend/app/ingestion/agent_contract.py`
- Modify: `backend/app/ingestion/agent_csv_adapter.py`
- Test: `backend/tests/unit/ingestion/test_agent_contract.py`
- Test: `backend/tests/unit/ingestion/test_agent_csv_adapter.py`
- Test: `backend/tests/integration/executions/test_csv_versioning.py`

**Interfaces:**

- Consumes: raw CSV row mappings and `AgentSourceRole`.
- Produces: `AgentContractRecord.stable_locator` equal to the persisted target `id`, or `csv:<physical-row>` when the target has no `id`.
- Preserves: existing `CsvTargetVersioner` behavior that adds and carries the `id` column.

- [ ] **Step 1: Add failing header-mapping, target-locator, and multi-round tests**

Add these behaviors to the existing ingestion tests:

```python
def test_number_header_wins_over_internal_id_column() -> None:
    mapping = AgentContractMapper().resolve_header_mapping(("category", "number", "id"))
    assert mapping["number"] == "number"


def test_target_uses_persisted_id_as_stable_locator(tmp_path: Path) -> None:
    path = tmp_path / "target.csv"
    path.write_text(
        "id,category,name,number,phone,email\n"
        "csv:37,teacher,测试教师,T-037,13800138000,t37@example.test\n",
        encoding="utf-8",
    )

    outcome = AgentCsvIngestionAdapter().inspect_csv(
        path=path,
        task_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        source_role=AgentSourceRole.TARGET,
        selected_entities=frozenset({AgentEntityKind.TEACHER}),
    )

    assert outcome.records[0].stable_locator == "csv:37"
    assert outcome.records[0].number == "T-037"
```

Add parameterized rejection coverage to `test_agent_csv_adapter.py`:

```python
@pytest.mark.parametrize(
    "rows,error",
    [
        (
            "id,category,name,number\n"
            ",teacher,甲,T-1\n",
            "non-empty stable id",
        ),
        (
            "id,category,name,number\n"
            "same,teacher,甲,T-1\n"
            "same,teacher,乙,T-2\n",
            "unique stable row identifiers",
        ),
    ],
)
def test_target_rejects_invalid_persisted_ids(
    tmp_path: Path,
    rows: str,
    error: str,
) -> None:
    path = tmp_path / "target.csv"
    path.write_text(rows, encoding="utf-8")

    with pytest.raises(AgentContractError, match=error):
        AgentCsvIngestionAdapter().inspect_csv(
            path=path,
            task_id=uuid4(),
            run_id=uuid4(),
            snapshot_id=uuid4(),
            tenant_id="school-1",
            source_role=AgentSourceRole.TARGET,
            selected_entities=frozenset(AgentEntityKind),
        )
```

In `test_csv_versioning.py`, import `AgentCsvIngestionAdapter`,
`AgentEntityKind`, and `AgentSourceRole`, then create a CSV without internal
IDs, delete its first record, ingest the derived file, and update the
remaining record using its original generated locator:

```python
@pytest.mark.asyncio
async def test_generated_locator_survives_an_earlier_row_deletion(
    tmp_path: Path,
) -> None:
    original = tmp_path / "target.csv"
    original.write_text(
        "category,name,number,phone,email\n"
        "teacher,甲,T-1,13800138001,t1@example.test\n"
        "teacher,乙,T-2,13800138002,t2@example.test\n",
        encoding="utf-8",
    )
    first_versioner = CsvTargetVersioner(
        repository=VersionRepositorySpy(),
        output_root=tmp_path / "first",
    )
    child = await first_versioner.derive(
        parent_version(original),
        (
            operation(
                OperationType.DISABLE,
                target="csv:2",
                before={"number": "T-1"},
                after={},
            ).model_copy(
                update={
                    "compensation_for": uuid4(),
                    "restore_absence": True,
                }
            ),
        ),
        batch_id=uuid4(),
    )

    outcome = AgentCsvIngestionAdapter().inspect_csv(
        path=Path(child.storage_path),
        task_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        source_role=AgentSourceRole.TARGET,
        selected_entities=frozenset({AgentEntityKind.TEACHER}),
    )
    assert outcome.records[0].stable_locator == "csv:3"

    second_versioner = CsvTargetVersioner(
        repository=VersionRepositorySpy(),
        output_root=tmp_path / "second",
    )
    updated = await second_versioner.derive(
        child,
        (
            operation(
                OperationType.UPDATE,
                target="csv:3",
                before={"name": "乙"},
                after={"name": "乙老师"},
            ),
        ),
        batch_id=uuid4(),
    )

    assert read_rows(Path(updated.storage_path))[0]["name"] == "乙老师"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/unit/ingestion/test_agent_contract.py \
  tests/unit/ingestion/test_agent_csv_adapter.py \
  tests/integration/executions/test_csv_versioning.py::test_generated_locator_survives_an_earlier_row_deletion \
  -q
```

Expected: the explicit `number` header test fails because `id` is still treated as a second number alias; the target locator and invalid-ID tests fail because ingestion still uses physical row locators and does not validate persisted IDs.

- [ ] **Step 3: Implement persisted target locator selection**

In `AgentContractMapper.resolve_header_mapping`, prefer a single non-`id` number header when an internal `id` column is also present:

```python
if canonical == "number" and len(matches) > 1:
    explicit_number_matches = [
        actual for actual in matches if actual.strip().casefold() != "id"
    ]
    if len(explicit_number_matches) == 1:
        matches = explicit_number_matches
```

In `agent_csv_adapter.py`, add a target-only locator resolver:

```python
def _target_stable_locator(row: Mapping[str, object], row_number: int) -> str:
    id_values = [
        value
        for key, value in row.items()
        if str(key).strip().casefold() == "id"
    ]
    if not id_values:
        return f"csv:{row_number}"
    value = id_values[0]
    locator = str(value).strip() if value is not None else ""
    if not locator:
        raise AgentContractError("target CSV requires a non-empty stable id")
    return locator
```

During `inspect_csv`, resolve and de-duplicate every target locator before selected-entity filtering, then copy it onto the mapped record:

```python
seen_target_locators: set[str] = set()

locator = (
    _target_stable_locator(raw_row, row_number)
    if source_role is AgentSourceRole.TARGET
    else f"csv:{row_number}"
)
if source_role is AgentSourceRole.TARGET:
    if locator in seen_target_locators:
        raise AgentContractError("target CSV requires unique stable row identifiers")
    seen_target_locators.add(locator)

record = self._mapper.map_row(...).model_copy(
    update={"stable_locator": locator}
)
```

- [ ] **Step 4: Run the ingestion tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/unit/ingestion/test_agent_contract.py \
  tests/unit/ingestion/test_agent_csv_adapter.py \
  tests/integration/executions/test_csv_versioning.py::test_generated_locator_survives_an_earlier_row_deletion \
  -q
```

Expected: all selected ingestion tests pass, and the second mutation updates the intended remaining record.

- [ ] **Step 5: Commit Task 1**

```bash
git add \
  backend/app/ingestion/agent_contract.py \
  backend/app/ingestion/agent_csv_adapter.py \
  backend/tests/unit/ingestion/test_agent_contract.py \
  backend/tests/unit/ingestion/test_agent_csv_adapter.py \
  backend/tests/integration/executions/test_csv_versioning.py
git commit -m "fix: preserve csv target locators across syncs"
```

---

### Task 2: Fail closed when a locator resolves to another entity

**Files:**

- Modify: `backend/app/agent_runtime/csv_governance_handlers.py`
- Test: `backend/tests/unit/agent_runtime/test_csv_governance_handlers.py`

**Interfaces:**

- Consumes: an `AgentInputRecord`-like subject and the canonical row returned by `read_target_rows`.
- Produces: no value on identity agreement; raises a precise `ValueError` before governance operation compilation on mismatch.

- [ ] **Step 1: Add failing identity-guard tests**

Extend the unit test helper with `stable_locator="csv:37"` and add:

```python
def test_target_identity_guard_accepts_the_analyzed_entity() -> None:
    _require_target_identity(
        _record(),
        {
            "category": "老师",
            "name": "测试教师",
            "number": "T-001",
        },
    )


def test_target_identity_guard_rejects_a_locator_for_another_entity() -> None:
    with pytest.raises(ValueError, match="different entity.*number"):
        _require_target_identity(
            _record(),
            {
                "category": "老师",
                "name": "其他教师",
                "number": "T-999",
            },
        )
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/agent_runtime/test_csv_governance_handlers.py -q
```

Expected: collection fails because `_require_target_identity` does not exist.

- [ ] **Step 3: Implement and wire the identity guard**

Import `normalize_identifier` and `normalize_null`, then add:

```python
def _require_target_identity(
    subject: AgentInputRecord,
    raw_target_values: Mapping[str, object],
) -> None:
    normalizers = {
        "category": normalize_null,
        "name": normalize_null,
        "number": normalize_identifier,
    }
    mismatches = [
        field
        for field, normalize in normalizers.items()
        if getattr(subject, field) is not None
        and normalize(
            str(raw_target_values[field])
            if raw_target_values.get(field) is not None
            else None
        )
        != normalize(getattr(subject, field))
    ]
    if mismatches:
        raise ValueError(
            "target stable locator resolved to a different entity: "
            + ", ".join(mismatches)
        )
```

In `_finding_inputs`, require a row to exist and call this guard for:

- the target side of every `field_difference`;
- every `target_extra`;
- every `target_duplicate`.

Do not fall back from a missing stable locator to `_record_values(subject)`, because that would conceal a broken target contract.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/agent_runtime/test_csv_governance_handlers.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run the CSV governance integration tests**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/integration/agent_runtime/test_csv_governance_worker.py \
  tests/integration/agent_runtime/test_csv_analysis_worker.py -q
```

Expected: all tests pass; any fixture whose locator does not identify its analyzed subject must be corrected rather than weakening the guard.

- [ ] **Step 6: Commit Task 2**

```bash
git add \
  backend/app/agent_runtime/csv_governance_handlers.py \
  backend/tests/unit/agent_runtime/test_csv_governance_handlers.py
git commit -m "fix: reject mismatched csv mutation targets"
```

---

### Task 3: Display approval, execution failure, and input anomaly independently

**Files:**

- Modify: `frontend/src/features/reports/AgentReportPage.tsx`
- Modify: `frontend/src/features/reports/AgentReportPage.test.tsx`
- Modify: `frontend/src/styles/apple.css`

**Interfaces:**

- Consumes: existing report facts `operator_decision`, `execution_status`, `excluded_findings`, and `mutation_summary`.
- Produces: separate status tags and a derived partial-completion presentation without changing the API contract.

- [ ] **Step 1: Add failing report-state tests**

Add a report fixture with:

```typescript
findings: [{
  id: "finding-failed",
  category_zh: "多余教师",
  entity_name: "测试教师",
  operator_decision: "approved",
  execution_status: "failed",
}],
excluded_findings: [{
  reason: "目标记录缺少身份字段",
  disposition: "target_extra",
}],
mutations: [{
  id: "operation-failed",
  operation: "delete",
  entity_kind: "teacher",
  status: "failed",
}],
mutation_summary: { succeeded: 0, failed: 1, verification_failed: 0 },
publication: { status: "no_changes" },
```

Assert:

```typescript
expect(await screen.findByText("部分完成")).toBeInTheDocument();
expect(screen.getByText("已同意")).toBeInTheDocument();
expect(screen.getAllByText("执行失败").length).toBeGreaterThan(0);
expect(screen.getByText("输入异常", { selector: ".ant-tag" })).toBeInTheDocument();
expect(container.querySelector(".agent-report-metric-error")).not.toBeNull();
```

Update the successful fixture with `execution_status: "succeeded"` and assert that `已同意` and `执行成功` are both visible.

- [ ] **Step 2: Run the component test and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/features/reports/AgentReportPage.test.tsx
```

Expected: the failed report still displays `同步完成`, the approved tag hides the execution tag, input anomalies lack a tag, and the failed metric lacks error styling.

- [ ] **Step 3: Implement independent report status tags**

In `AgentReportPage.tsx`:

1. Add numeric coercion for mutation summary counts.
2. Define `failedMutationCount` as `failed + verification_failed`.
3. Render `部分完成` with an error color when `failedMutationCount > 0`.
4. Build a de-duplicated array of meaningful approval and execution states for each finding.
5. Render every state with its own `Tag`.
6. Render each exclusion row with its reason and an orange `输入异常` tag.
7. Apply `agent-report-metric-error` to the failed metric when its count is positive.

Use this color policy:

```typescript
function reportStatusColor(status: string) {
  if (status === "approved" || status === "succeeded" || status === "already_restored") {
    return "success";
  }
  if (status === "blocked" || status === "conflict_skipped" || status === "not_executed") {
    return "warning";
  }
  return "error";
}
```

Do not use `operator_decision` as a fallback for a missing execution result.

- [ ] **Step 4: Add the focused layout styles**

In `apple.css`:

```css
.agent-report-status-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.agent-report-exclusions > li {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.agent-report-metrics article.agent-report-metric-error {
  border-color: #efc6c1;
  background: var(--codex-danger-soft);
}

.agent-report-metric-error span,
.agent-report-metric-error strong {
  color: var(--codex-danger);
}
```

- [ ] **Step 5: Run the component test and verify GREEN**

Run:

```bash
cd frontend
npm test -- --run src/features/reports/AgentReportPage.test.tsx
```

Expected: all report tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add \
  frontend/src/features/reports/AgentReportPage.tsx \
  frontend/src/features/reports/AgentReportPage.test.tsx \
  frontend/src/styles/apple.css
git commit -m "fix: surface partial agent report outcomes"
```

---

### Task 4: Verify the integrated change

**Files:**

- Verify only; no planned production edits.

**Interfaces:**

- Consumes: all changes from Tasks 1–3.
- Produces: fresh backend, frontend, type, lint, and build evidence.

- [ ] **Step 1: Run focused backend verification**

```bash
cd backend
.venv/bin/pytest \
  tests/unit/ingestion/test_agent_contract.py \
  tests/unit/ingestion/test_agent_csv_adapter.py \
  tests/unit/agent_runtime/test_csv_governance_handlers.py \
  tests/integration/executions/test_csv_versioning.py \
  tests/integration/agent_runtime/test_csv_governance_worker.py \
  tests/integration/agent_runtime/test_csv_analysis_worker.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run backend static checks**

```bash
cd backend
.venv/bin/ruff check app/ingestion/agent_contract.py \
  app/ingestion/agent_csv_adapter.py \
  app/agent_runtime/csv_governance_handlers.py \
  tests/unit/ingestion/test_agent_contract.py \
  tests/unit/ingestion/test_agent_csv_adapter.py \
  tests/unit/agent_runtime/test_csv_governance_handlers.py \
  tests/integration/executions/test_csv_versioning.py
.venv/bin/mypy app
```

Expected: both commands exit successfully.

- [ ] **Step 3: Run frontend verification**

```bash
cd frontend
npm test -- --run src/features/reports/AgentReportPage.test.tsx
npm run lint
npm run typecheck
npm run build
```

Expected: all commands exit successfully.

- [ ] **Step 4: Inspect the final diff**

```bash
git status --short
git diff --check HEAD~3..HEAD
git diff --stat HEAD~3..HEAD
```

Expected: only scoped implementation and test files plus the approved specification and plan are present; the user's pre-existing E2E edits remain uncommitted and unchanged.

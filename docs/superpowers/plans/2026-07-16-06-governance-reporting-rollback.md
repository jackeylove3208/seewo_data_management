# Governance Reporting and Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate versioned governance reports on demand from fixed execution facts and safely roll an execution back through a separately approved, verified compensation batch.

**Architecture:** Reporting reads an immutable execution fact bundle, optionally asks the governed Agent for narrative text, validates it, and renders HTML without consulting current target state. Rollback first computes deterministic drift, dependency, reversibility, and affected-scope results; when permitted, it reverses operations in dependency-safe order, creates a new governance plan, and reuses module 5 confirmation/execution/verification. Original executions, reports, and target versions remain immutable.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL JSONB, Jinja2, pytest.

## Global Constraints

- Reports are optional and start only from an explicit user request on a completed or partially completed execution.
- Report facts come from execution-time snapshots, analyses, plans, attempts, operator identity, and rollback links.
- Current mutable target state may be mentioned only as separately timestamped context; it cannot replace historical facts.
- Every report has a version and preserves model/Skill/prompt provenance when AI narrative is used.
- Every rollback runs preflight before confirmation.
- Later dependencies, target drift, irreversible operations, or conflicting entities block direct rollback.
- Rollback is a new compensation plan and execution batch; it never edits/deletes the original record.
- Compensation operations execute in reverse dependency order and pass the same policy, confirmation, idempotency, and verification gates as ordinary execution.
- Report and rollback actor identity comes from backend authentication context.

---

## File Map

- `backend/app/schemas/reports.py`: report facts/content and rollback preflight/plan responses.
- `backend/app/models/reports.py`: report jobs/versions and rollback links.
- `backend/app/reports/`: fact collection, optional narrative generation, and HTML rendering.
- `backend/app/executions/compensation.py`: rollback preflight and inverse operation construction.
- `backend/app/api/routes/reports.py` and `rollbacks.py`: on-demand actions, status, content, preflight, confirmation.

### Task 1: Define report and rollback persistence

**Files:**
- Create: `backend/app/schemas/reports.py`
- Create: `backend/app/models/reports.py`
- Create: `backend/app/repositories/reports.py`
- Create: `backend/alembic/versions/0007_reporting_rollback.py`
- Test: `backend/tests/integration/repositories/test_reports.py`

**Interfaces:**
- Consumes: execution ID, fixed fact hash, report version, actor, render metadata, original/compensation batch IDs.
- Produces: immutable `GovernanceReport`, `ReportJob`, `RollbackPreflightResult`, and `RollbackLink`.

- [ ] **Step 1: Write version and link tests**

```python
async def test_report_versions_append(repo, completed_execution) -> None:
    first = await repo.create_version(completed_execution.id, facts_hash="a" * 64, requested_by="operator-1")
    second = await repo.create_version(completed_execution.id, facts_hash="a" * 64, requested_by="operator-1")
    assert (first.version, second.version) == (1, 2)

async def test_original_and_compensation_have_one_link(repo, original, compensation) -> None:
    link = await repo.link_rollback(original.id, compensation.id)
    assert link.original_execution_id == original.id
    with pytest.raises(IntegrityError):
        await repo.link_rollback(original.id, compensation.id)
```

- [ ] **Step 2: Run repository tests**

Run: `cd backend && uv run pytest tests/integration/repositories/test_reports.py -q`

Expected: FAIL because reporting persistence is absent.

- [ ] **Step 3: Define structured facts and report contracts**

```python
class ExecutionFactBundle(BaseModel):
    execution_id: UUID
    task_id: UUID
    source_snapshot_id: UUID
    target_input_version_id: UUID
    target_output_version_id: UUID | None
    plan_id: UUID
    plan_version: int
    operator_id: str
    started_at: datetime
    completed_at: datetime
    difference_statistics: dict[str, int]
    analyses: tuple[dict, ...]
    operations: tuple[dict, ...]
    failures: tuple[dict, ...]
    rollback_status: str

class GovernanceReportContent(BaseModel):
    summary: str
    causes: tuple[str, ...]
    actions: tuple[str, ...]
    outcomes: tuple[str, ...]
    failures: tuple[str, ...]
    rollback_status: str
```

- [ ] **Step 4: Add append-only report and rollback tables**

Create `report_jobs`, `governance_reports`, and `rollback_links`. Store execution ID, version, status, facts JSON/hash, content JSON, HTML path/hash, requested/generated actor/time, AI provenance, and original/compensation links. Add unique `(execution_id, version)` and unique original/compensation link constraints.

- [ ] **Step 5: Migrate, verify, and commit**

Run: `cd backend && uv run alembic upgrade head && uv run pytest tests/integration/repositories/test_reports.py -q`

Expected: versioning, immutability, actor, JSON, duplicate link, and transaction rollback tests PASS.

```bash
git add backend/app/schemas/reports.py backend/app/models/reports.py backend/app/repositories/reports.py backend/alembic backend/tests/integration/repositories/test_reports.py
git commit -m "feat: persist reports and rollback links"
```

### Task 2: Collect immutable execution-time report facts

**Files:**
- Create: `backend/app/reports/facts.py`
- Test: `backend/tests/integration/reports/test_fact_collector.py`

**Interfaces:**
- Consumes: execution record repositories only.
- Produces: canonical `ExecutionFactBundle` and stable SHA-256 facts hash.

- [ ] **Step 1: Test facts remain historical after target changes**

```python
async def test_fact_bundle_ignores_current_target(collector, execution, target_mutator) -> None:
    before = await collector.collect(execution.id)
    await target_mutator.change_teacher_name("t-1", "later change")
    after = await collector.collect(execution.id)
    assert after == before
    assert after.target_output_version_id == execution.output_version_id

async def test_operator_and_failures_are_included(collector, partial_execution) -> None:
    facts = await collector.collect(partial_execution.id)
    assert facts.operator_id == "demo-operator"
    assert len(facts.failures) == 1
```

- [ ] **Step 2: Run fact tests**

Run: `cd backend && uv run pytest tests/integration/reports/test_fact_collector.py -q`

Expected: FAIL because fact collector is missing.

- [ ] **Step 3: Implement one-snapshot database read**

```python
class ExecutionFactCollector:
    async def collect(self, execution_id: UUID) -> ExecutionFactBundle:
        async with self.session.begin():
            execution = await self.executions.get_detail_or_404(execution_id)
            if execution.status not in {"succeeded", "partial_failure", "failed"}:
                raise ReportNotAllowed("execution has not reached a reportable terminal state")
            return ExecutionFactBundle(
                execution_id=execution.id, task_id=execution.task_id,
                source_snapshot_id=execution.source_snapshot_id,
                target_input_version_id=execution.input_version_id,
                target_output_version_id=execution.output_version_id,
                plan_id=execution.plan_id, plan_version=execution.plan_version,
                operator_id=execution.operator_id, started_at=execution.started_at,
                completed_at=execution.completed_at,
                difference_statistics=count_differences(execution.operations),
                analyses=tuple(op.analysis for op in execution.operations),
                operations=tuple(operation_fact(op) for op in execution.operations),
                failures=tuple(failure_fact(op) for op in execution.operations if op.latest_status != "succeeded"),
                rollback_status=execution.rollback_status,
            )
```

- [ ] **Step 4: Add canonical hash and commit**

```python
def facts_hash(facts: ExecutionFactBundle) -> str:
    payload = facts.model_dump_json(exclude_none=False).encode("utf-8")
    return sha256(payload).hexdigest()
```

Run: `cd backend && uv run pytest tests/integration/reports/test_fact_collector.py -q`

Expected: historical consistency, partial failure, operator, attempts, snapshot refs, and stable hash tests PASS.

```bash
git add backend/app/reports/facts.py backend/tests/integration/reports/test_fact_collector.py
git commit -m "feat: collect immutable execution report facts"
```

### Task 3: Generate and render versioned reports on demand

**Files:**
- Create: `backend/app/reports/generator.py`
- Create: `backend/app/reports/renderer.py`
- Create: `backend/app/reports/templates/governance-report.html.j2`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/integration/reports/test_generator.py`

**Interfaces:**
- Consumes: fixed `ExecutionFactBundle`, `generate-governance-report@1.0.0` Skill, governed Agent, report storage.
- Produces: validated `GovernanceReportContent`, immutable HTML, content hash, provenance; deterministic fallback if narrative generation fails.

- [ ] **Step 1: Write on-demand and fixed-input tests**

```python
async def test_no_report_exists_before_request(repo, execution) -> None:
    assert await repo.list_for_execution(execution.id) == []

async def test_generator_uses_saved_facts(generator, execution, target_mutator) -> None:
    report = await generator.generate(execution.id, requested_by="demo-operator")
    await target_mutator.change_all()
    assert (await generator.get(report.id)).facts_hash == report.facts_hash
    assert "demo-operator" in report.html_path.read_text()

async def test_invalid_model_narrative_uses_deterministic_content(generator, bad_model, execution) -> None:
    report = await generator.generate(execution.id, requested_by="demo-operator")
    assert report.status == "succeeded"
    assert report.provenance["mode"] == "deterministic_fallback"
```

- [ ] **Step 2: Run generator tests**

Run: `cd backend && uv add 'jinja2>=3.1,<4' && uv run pytest tests/integration/reports/test_generator.py -q`

Expected: FAIL because generator and renderer are missing.

- [ ] **Step 3: Implement fact-constrained generation**

```python
class ReportGenerator:
    async def generate(self, execution_id: UUID, requested_by: str) -> GovernanceReport:
        facts = await self.facts.collect(execution_id)
        version = await self.reports.start(execution_id, facts, facts_hash(facts), requested_by)
        try:
            agent_result = await self.agent.run_structured(
                AgentRequest(
                    skill_name="generate-governance-report", skill_version="1.0.0",
                    input_payload=facts.model_dump(mode="json"), tool_context=None,
                ),
                GovernanceReportContent,
            )
            content = GovernanceReportContent.model_validate(agent_result.output)
            provenance = agent_result.provenance
        except (ModelProviderError, ValidationError, AnalysisPolicyError):
            content = deterministic_report(facts)
            provenance = {"mode": "deterministic_fallback"}
        html = self.renderer.render(facts, content, version.version)
        return await self.reports.finish(version.id, content, html, provenance)
```

- [ ] **Step 4: Render an inspectable HTML report**

```python
class HtmlReportRenderer:
    def __init__(self, template_root: Path) -> None:
        self.environment = Environment(
            loader=FileSystemLoader(template_root),
            autoescape=select_autoescape(enabled_extensions=("html", "j2"), default=True),
        )

    def render(self, facts: ExecutionFactBundle, content: GovernanceReportContent, report_version: int) -> str:
        return self.environment.get_template("governance-report.html.j2").render(
            facts=facts.model_dump(mode="json"), content=content.model_dump(mode="json"),
            report_version=report_version,
        )
```

```html
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>治理报告 {{ report_version }}</title></head>
<body>
  <h1>组织数据治理报告</h1>
  <dl><dt>执行记录</dt><dd>{{ facts.execution_id }}</dd><dt>操作人</dt><dd>{{ facts.operator_id }}</dd></dl>
  <h2>差异统计</h2><pre>{{ facts.difference_statistics | tojson(indent=2) }}</pre>
  <h2>治理结果</h2><p>{{ content.summary }}</p>
  <h2>失败项</h2><pre>{{ facts.failures | tojson(indent=2) }}</pre>
  <h2>回滚状态</h2><p>{{ content.rollback_status }}</p>
</body></html>
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/integration/reports/test_generator.py -q`

Expected: on-demand, version, fixed facts, structured output, fallback, escaping, file hash, and operator tests PASS.

```bash
git add backend/app/reports backend/tests/integration/reports/test_generator.py
git commit -m "feat: generate versioned governance reports"
```

### Task 4: Implement deterministic rollback preflight

**Files:**
- Create: `backend/app/executions/rollback_preflight.py`
- Test: `backend/tests/integration/executions/test_rollback_preflight.py`

**Interfaces:**
- Consumes: original execution, current target version/state, later executions, dependencies, reversibility metadata.
- Produces: `RollbackPreflightResult(allowed, conflicts, affected_scope, reverse_order, current_target_version)` persisted for confirmation.

- [ ] **Step 1: Write each blocking scenario**

```python
async def test_later_dependency_blocks_rollback(preflight, created_department, later_class) -> None:
    result = await preflight.check(created_department.execution_id)
    assert result.allowed is False
    assert result.conflicts[0].code == "later_dependency"
    assert result.conflicts[0].entity_ids == (later_class.entity_id,)

async def test_target_drift_blocks_rollback(preflight, execution, target_mutator) -> None:
    await target_mutator.change(execution.operations[0].target_entity_id)
    result = await preflight.check(execution.id)
    assert any(c.code == "target_drift" for c in result.conflicts)

async def test_clean_execution_returns_reverse_dependency_order(preflight, parent_child_execution) -> None:
    result = await preflight.check(parent_child_execution.id)
    assert result.allowed is True
    assert result.reverse_order == tuple(reversed(parent_child_execution.forward_order))
```

- [ ] **Step 2: Run rollback preflight tests**

Run: `cd backend && uv run pytest tests/integration/executions/test_rollback_preflight.py -q`

Expected: FAIL because rollback preflight is missing.

- [ ] **Step 3: Define explicit result types**

```python
class RollbackConflictCode(StrEnum):
    LATER_DEPENDENCY = "later_dependency"
    TARGET_DRIFT = "target_drift"
    IRREVERSIBLE = "irreversible"
    CONFLICTING_ENTITY = "conflicting_entity"
    ALREADY_ROLLED_BACK = "already_rolled_back"

class RollbackConflict(BaseModel):
    code: RollbackConflictCode
    operation_id: UUID | None = None
    entity_ids: tuple[UUID, ...] = ()
    message: str

class RollbackPreflightResult(BaseModel):
    execution_id: UUID
    current_target_version_id: UUID
    allowed: bool
    conflicts: tuple[RollbackConflict, ...]
    affected_scope: dict[EntityType, int]
    reverse_order: tuple[UUID, ...]
    content_hash: str
```

- [ ] **Step 4: Implement preflight from persisted facts plus current state**

```python
async def check(self, execution_id: UUID) -> RollbackPreflightResult:
    original = await self.executions.get_detail_or_404(execution_id)
    current = await self.target.current_version()
    conflicts = []
    conflicts.extend(await self._later_dependencies(original))
    conflicts.extend(await self._drift(original, current))
    conflicts.extend(self._irreversible(original))
    if original.rollback_status != "not_rolled_back":
        conflicts.append(RollbackConflict(code="already_rolled_back", message="execution already has rollback state"))
    reverse = tuple(op.id for op in reversed(self.graph.order(original.successful_operations)))
    return build_preflight(original, current, conflicts, reverse)
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/integration/executions/test_rollback_preflight.py -q`

Expected: later dependency, drift, irreversible, conflict, already-rolled-back, partial execution scope, and clean rollback tests PASS.

```bash
git add backend/app/executions/rollback_preflight.py backend/tests/integration/executions/test_rollback_preflight.py
git commit -m "feat: preflight rollback impact"
```

### Task 5: Build and execute compensation plans

**Files:**
- Create: `backend/app/executions/compensation.py`
- Test: `backend/tests/integration/executions/test_compensation.py`

**Interfaces:**
- Consumes: allowed preflight hash, original successful operations, backend operator.
- Produces: inverse `GovernancePlan`, new confirmed execution batch, verified target version, and immutable `RollbackLink`.

- [ ] **Step 1: Write inverse and immutable-original tests**

```python
@pytest.mark.parametrize(("forward", "inverse"), [
    ("create", "disable"), ("update", "update"), ("move", "move"), ("disable", "update"),
])
def test_inverse_operation_types(compensation, operation_factory, forward, inverse) -> None:
    operation = operation_factory(operation_type=forward)
    assert compensation.inverse(operation).operation_type.value == inverse

async def test_compensation_creates_new_record(service, rollbackable_execution) -> None:
    before = rollbackable_execution.model_copy(deep=True)
    result = await service.execute_rollback(rollbackable_execution.id, valid_confirmation(), operator_id="demo-operator")
    assert result.id != rollbackable_execution.id
    assert result.output_version.sha256 == rollbackable_execution.input_version.sha256
    assert (await service.executions.get(rollbackable_execution.id)).model_dump() == before.model_dump()
```

- [ ] **Step 2: Run compensation tests**

Run: `cd backend && uv run pytest tests/integration/executions/test_compensation.py -q`

Expected: FAIL because compensation builder is missing.

- [ ] **Step 3: Implement before/after reversal**

```python
def inverse(operation: GovernanceOperation) -> GovernanceOperation:
    if not operation.reversible:
        raise IrreversibleOperation(operation.id)
    inverse_type = {
        OperationType.CREATE: OperationType.DISABLE,
        OperationType.UPDATE: OperationType.UPDATE,
        OperationType.MOVE: OperationType.MOVE,
        OperationType.DISABLE: OperationType.UPDATE,
    }[operation.operation_type]
    return GovernanceOperation(
        difference_id=operation.difference_id, difference_version=operation.difference_version,
        operation_type=inverse_type, entity_type=operation.entity_type,
        target_entity_id=operation.target_entity_id, before=operation.after, after=operation.before,
        changed_fields=operation.changed_fields, dependencies=frozenset(), reversible=True, risk=RiskLevel.HIGH,
        compensation_for=operation.id,
        restore_absence=operation.operation_type is OperationType.CREATE,
    )
```

For the CSV connector, `restore_absence=True` removes the row created by the compensated operation so the clean rollback output hash equals the original input version. The plan validator permits this flag only on a compensation plan linked to that exact successful create operation; normal disable operations never remove rows.

- [ ] **Step 4: Reuse ordinary execution and link only after verification**

```python
async def execute_rollback(self, execution_id, confirmation, operator_id):
    preflight = await self.preflight.get_exact(confirmation.preflight_hash)
    if not preflight.allowed:
        raise RollbackBlocked(preflight.conflicts)
    original = await self.executions.get_detail_or_404(execution_id)
    operations = tuple(self.builder.inverse(op) for op in reversed(original.successful_operations))
    plan = await self.plans.save_compensation(original, operations)
    batch = await self.execution.confirm_compensation(plan, operator_id, confirmation.idempotency_key)
    result = await self.execution.execute(batch.id)
    await self.rollbacks.link(original.id, result.id, status=result.status)
    return result
```

- [ ] **Step 5: Verify successful and failed compensation and commit**

Run: `cd backend && uv run pytest tests/integration/executions/test_compensation.py -q`

Expected: all inverse types, reverse order, stale preflight, failed verification, partial compensation, link, and immutable-original tests PASS.

```bash
git add backend/app/executions/compensation.py backend/tests/integration/executions/test_compensation.py
git commit -m "feat: execute rollback as compensation"
```

### Task 6: Expose report and rollback APIs

**Files:**
- Create: `backend/app/api/routes/reports.py`
- Create: `backend/app/api/routes/rollbacks.py`
- Modify: `backend/app/api/routes/execution_records.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/api/test_reports_rollbacks.py`

**Interfaces:**
- Consumes: execution ID, idempotency keys, preflight hash, high-risk acknowledgement, backend operator.
- Produces: report create/status/detail/download, rollback preflight/confirm/status, and permitted actions on execution detail.

- [ ] **Step 1: Write explicit-action and conflict API tests**

```python
def test_report_is_created_only_by_post(client, completed_execution) -> None:
    assert client.get(f"/api/execution-records/{completed_execution.id}/reports").json()["items"] == []
    response = client.post(f"/api/execution-records/{completed_execution.id}/reports", headers={"Idempotency-Key": "report-1"})
    assert response.status_code == 202

def test_conflicted_rollback_cannot_confirm(client, conflicted_execution) -> None:
    preflight = client.post(f"/api/execution-records/{conflicted_execution.id}/rollback-preflight").json()
    response = client.post(f"/api/execution-records/{conflicted_execution.id}/rollbacks", json={
        "preflight_hash": preflight["content_hash"], "high_risk_acknowledged": True,
    })
    assert response.status_code == 409
```

- [ ] **Step 2: Run API tests**

Run: `cd backend && uv run pytest tests/integration/api/test_reports_rollbacks.py -q`

Expected: FAIL because routes are absent.

- [ ] **Step 3: Add on-demand report routes**

```python
@router.post("/execution-records/{execution_id}/reports", status_code=202)
async def create_report(execution_id: UUID, key: Annotated[str, Header(alias="Idempotency-Key")],
                        operator=Depends(current_operator), service=Depends(get_report_service)):
    return await service.request(execution_id, operator.id, key)

@router.get("/reports/{report_id}", response_model=GovernanceReportResponse)
async def get_report(report_id: UUID, repo=Depends(get_report_repo)):
    return await repo.get_or_404(report_id)
```

- [ ] **Step 4: Add preflight and compensation routes**

```python
@router.post("/execution-records/{execution_id}/rollback-preflight", response_model=RollbackPreflightResult)
async def rollback_preflight(execution_id: UUID, service=Depends(get_rollback_service)):
    return await service.preflight(execution_id)

@router.post("/execution-records/{execution_id}/rollbacks", status_code=202)
async def confirm_rollback(execution_id: UUID, body: ConfirmRollbackRequest,
                           operator=Depends(current_operator), service=Depends(get_rollback_service)):
    return await service.confirm(execution_id, body, operator.id)
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/integration/api/test_reports_rollbacks.py -q`

Expected: report state, versioning, download, backend actor, rollback conflicts, stale preflight, confirmation, link, and permitted-action tests PASS.

```bash
git add backend/app/api/routes/reports.py backend/app/api/routes/rollbacks.py backend/app/api/routes/execution_records.py backend/app/main.py backend/tests/integration/api/test_reports_rollbacks.py
git commit -m "feat: expose reports and compensation rollback"
```

### Task 7: Add historical consistency and rollback acceptance coverage

**Files:**
- Create: `backend/tests/e2e/test_report_and_rollback.py`
- Create: `backend/tests/fixtures/rollback_cases.py`

**Interfaces:**
- Consumes: succeeded, partial-failure, drifted, dependent, and clean synthetic executions.
- Produces: end-to-end regression proof for optional report and compensation semantics.

- [ ] **Step 1: Write complete report and rollback flow**

```python
async def test_report_then_rollback_preserves_all_versions(client, rollbackable_execution) -> None:
    report_job = await client.post(f"/api/execution-records/{rollbackable_execution.id}/reports",
                                   headers={"Idempotency-Key": "report-e2e"})
    report = await finish_report(report_job.json()["id"])
    preflight = await client.post(f"/api/execution-records/{rollbackable_execution.id}/rollback-preflight")
    rollback = await client.post(f"/api/execution-records/{rollbackable_execution.id}/rollbacks", json={
        "preflight_hash": preflight.json()["content_hash"], "high_risk_acknowledged": True,
    }, headers={"Idempotency-Key": "rollback-e2e"})
    result = await run_batch(rollback.json()["id"])
    assert result.status == "succeeded"
    assert report.json()["execution_id"] == str(rollbackable_execution.id)
    assert await original_execution_is_unchanged(rollbackable_execution.id)
    assert await target_versions_form_chain(length=3)
```

- [ ] **Step 2: Run all module tests**

Run: `cd backend && uv run pytest tests/integration/reports tests/integration/executions/test_rollback_preflight.py tests/integration/executions/test_compensation.py tests/integration/api/test_reports_rollbacks.py tests/e2e/test_report_and_rollback.py -q`

Expected: historical report consistency, blocked rollback, successful/failed compensation, immutable originals, and version-chain tests PASS.

- [ ] **Step 3: Commit acceptance coverage**

```bash
git add backend/tests/e2e/test_report_and_rollback.py backend/tests/fixtures/rollback_cases.py
git commit -m "test: verify reports and compensation rollback"
```

## Module Acceptance

Run: `cd backend && uv run pytest tests/integration/reports tests/integration/executions/test_rollback_preflight.py tests/integration/executions/test_compensation.py tests/integration/api/test_reports_rollbacks.py tests/e2e/test_report_and_rollback.py -q && uv run ruff check . && uv run mypy app`

Expected: reports do not exist until requested, always identify the operator and fixed versions, later target changes do not rewrite history, unsafe rollbacks are blocked with entity-level reasons, and allowed rollback creates a separately audited verified batch.

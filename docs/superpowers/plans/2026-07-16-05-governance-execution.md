# Governance Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn operator-reviewed AI or manual governance proposals into policy-valid, dependency-ordered operations that produce a new verified Seewo CSV version and immutable execution audit records.

**Architecture:** `ai-new-ui` persists an operator-reviewed AI option or whitelisted manual edit as an immutable `pending_execution` proposal. A deterministic plan builder translates exact proposal versions into allowed operations; model advice is evidence, never executable instructions. Preview binds task, snapshots, proposals, differences, plan version, and backend-authenticated confirmer. Preflight re-reads proposal and target versions plus expected before-values immediately before a per-operation executor derives a new CSV, verifies it through the target connector, and records success, partial failure, retry eligibility, and immutable history. An optional plan explanation reuses the AI analysis enterprise model with a separate read-only Skill, but its failure never blocks preview or execution.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL, Polars, pytest.

## Global Constraints

- Only active `pending_execution` proposal versions backed by a current difference and mandatory analysis are eligible.
- AI and operator-authored proposals pass through the same field, operation, risk, dependency, and version policies.
- Proposal creator, batch confirmer, and optional independent reviewer come from backend context and are never accepted as client identity fields.
- Supported mutation operations are `create`, `update`, `move`, `disable`, and `skip`; `manual_review` is an ineligible state and `delete` is forbidden.
- Agent output cannot bypass allowed-field, operation, risk, dependency, reversibility, approval, or preflight policies.
- Preview and confirmation bind exact proposal, difference, target snapshot, and plan versions.
- First release permits the same operator to create/review a proposal and confirm its batch; high-risk operations require separate explicit acknowledgement and the model reserves an optional independent reviewer.
- Optional plan explanation uses the same configured enterprise provider and tokenization boundary as AI analysis but a separate Skill and schema; model failure is non-blocking.
- Preflight checks proposal/difference versions, target hash/version, expected before-values, dependencies, conflicts, and eligibility immediately before mutation.
- Parent operations run before dependent children.
- Uploaded target CSV is immutable; every batch derives a new child version.
- Every operation is idempotent, independently audited, and verified after connector reload.
- Partial success is preserved; retry processes only explicitly retryable failures.

---

## File Map

- `backend/app/schemas/executions.py`: operation, plan, preview, preflight, execution, and history contracts.
- `backend/app/models/executions.py`: plans, batches, operation attempts, target versions, audit events.
- `backend/app/governance/`: plan builder, policy validator, risk policy, dependency graph.
- `backend/app/governance/plan_explainer.py`: optional read-only, tokenized explanation using the shared enterprise provider.
- `backend/app/executions/`: preflight, CSV versioning, executor, and verifier.
- `backend/app/api/routes/execution_batches.py` and `execution_records.py`: preview, confirm, retry, history, detail, downloads.

### Task 1: Define governance operation and execution persistence

**Files:**
- Create: `backend/app/schemas/executions.py`
- Create: `backend/app/models/executions.py`
- Create: `backend/app/repositories/executions.py`
- Create: the next Alembic revision after the `ai-new-ui` migrations, named `<revision>_governance_execution.py`
- Test: `backend/tests/integration/repositories/test_executions.py`

**Interfaces:**
- Consumes: task/snapshot IDs, proposal/difference/analysis versions, proposal source, operation data, plan version, backend approval identities, attempts, verification.
- Produces: append-only `GovernancePlan`, `ExecutionBatch`, `ExecutionOperation`, `OperationAttempt`, and `TargetVersion` records.

- [ ] **Step 1: Write schema and append-only tests**

```python
def test_delete_is_not_an_operation() -> None:
    with pytest.raises(ValidationError):
        GovernanceOperation(operation_type="delete", entity_type="teacher", difference_id=uuid4(), difference_version=1)

async def test_attempts_append_instead_of_overwrite(repo, failed_operation) -> None:
    first = await repo.append_attempt(failed_operation.id, status="failed", error_code="timeout")
    second = await repo.append_attempt(failed_operation.id, status="succeeded")
    assert [a.attempt_number for a in await repo.list_attempts(failed_operation.id)] == [1, 2]
    assert first.error_code == "timeout"
```

- [ ] **Step 2: Run execution model tests**

Run: `cd backend && uv run pytest tests/integration/repositories/test_executions.py -q`

Expected: FAIL because execution contracts are absent.

- [ ] **Step 3: Define strict operation and batch contracts**

```python
class OperationType(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    MOVE = "move"
    DISABLE = "disable"
    SKIP = "skip"

class OperationStatus(StrEnum):
    PENDING = "pending"
    BLOCKED = "blocked"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    VERIFICATION_FAILED = "verification_failed"

class GovernanceOperation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    proposal_id: UUID
    proposal_version: int = Field(ge=1)
    proposal_source: Literal["ai", "operator"]
    difference_id: UUID
    difference_version: int = Field(ge=1)
    operation_type: OperationType
    entity_type: EntityType
    target_entity_id: UUID | None = None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    changed_fields: frozenset[str] = frozenset()
    dependencies: frozenset[UUID] = frozenset()
    reversible: bool
    risk: RiskLevel
    compensation_for: UUID | None = None
    restore_absence: bool = False

class GovernancePlan(BaseModel):
    id: UUID
    version: int
    task_id: UUID
    source_snapshot_id: UUID
    target_snapshot_id: UUID
    proposal_versions: tuple[ProposalVersionRef, ...]
    operations: tuple[GovernanceOperation, ...]
    content_hash: str
```

- [ ] **Step 4: Add normalized execution tables**

Create `governance_plans`, `execution_batches`, `execution_operations`, `operation_attempts`, `target_versions`, and `execution_audit_events`. Reference existing `governance_proposals` rather than duplicating proposal payloads. Add uniqueness on plan content hash per task, batch idempotency key, `(batch_id, operation_id)`, and `(operation_id, attempt_number)`.

```python
class OperationAttempt(Base, TimestampMixin):
    __tablename__ = "operation_attempts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(ForeignKey("execution_operations.id"), index=True)
    attempt_number: Mapped[int]
    status: Mapped[str]
    error_code: Mapped[str | None]
    error_detail: Mapped[dict | None] = mapped_column(JSONB)
    actual_after: Mapped[dict | None] = mapped_column(JSONB)
    verification: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("operation_id", "attempt_number"),)
```

- [ ] **Step 5: Migrate, verify, and commit**

Run: `cd backend && uv run alembic upgrade head && uv run pytest tests/integration/repositories/test_executions.py -q`

Expected: enum, constraints, immutable plan, append-only attempts, and transaction rollback tests PASS.

```bash
git add backend/app/schemas/executions.py backend/app/models/executions.py backend/app/repositories/executions.py backend/alembic backend/tests/integration/repositories/test_executions.py
git commit -m "feat: persist governance execution records"
```

### Task 2: Build and validate deterministic governance plans

**Files:**
- Create: `backend/app/governance/plan_builder.py`
- Create: `backend/app/governance/plan_validator.py`
- Create: `backend/app/governance/operation_policy.py`
- Test: `backend/tests/unit/governance/test_plan_builder.py`

**Interfaces:**
- Consumes: selected `(proposal_id, version)` pairs, referenced current analysis and difference evidence, target snapshot, operator context.
- Produces: validated and content-hashed `GovernancePlan`; rejects stale, superseded, unresolved, disallowed, or cross-task proposals.

- [ ] **Step 1: Write selection and policy tests**

```python
async def test_ai_missing_proposal_builds_create(builder, ai_missing_proposal) -> None:
    plan = await builder.build(ai_missing_proposal.task_id, [ai_missing_proposal.version_ref])
    assert plan.operations[0].operation_type is OperationType.CREATE
    assert plan.operations[0].proposal_source == "ai"
    assert plan.operations[0].after["name"] == ai_missing_proposal.authoritative_value["name"]

async def test_operator_proposal_cannot_change_disallowed_field(builder, operator_proposal_factory) -> None:
    proposal = operator_proposal_factory(operation_type="update", changes={"internal_admin": True})
    with pytest.raises(PlanPolicyError, match="internal_admin"):
        await builder.build(proposal.task_id, [proposal.version_ref])

async def test_unresolved_manual_review_cannot_build_plan(builder, manual_review_difference) -> None:
    with pytest.raises(PlanPolicyError, match="pending_execution proposal"):
        await builder.build(manual_review_difference.task_id, [])
```

- [ ] **Step 2: Run plan tests**

Run: `cd backend && uv run pytest tests/unit/governance/test_plan_builder.py -q`

Expected: FAIL because plan builder is missing.

- [ ] **Step 3: Define difference-to-operation and allowed-field policy**

```python
OPERATION_POLICY = {
    DifferenceType.SEEWO_MISSING: frozenset({OperationType.CREATE, OperationType.SKIP}),
    DifferenceType.SEEWO_REDUNDANT: frozenset({OperationType.DISABLE, OperationType.SKIP}),
    DifferenceType.ATTRIBUTE_CONFLICT: frozenset({OperationType.UPDATE, OperationType.SKIP}),
    DifferenceType.STRUCTURE_CONFLICT: frozenset({OperationType.MOVE, OperationType.SKIP}),
    DifferenceType.DUPLICATE_CONFLICT: frozenset({OperationType.DISABLE, OperationType.SKIP}),
}

ALLOWED_FIELDS = {
    EntityType.TEACHER: frozenset({"name", "employee_number", "phone", "email", "department_source_id", "status"}),
    EntityType.STUDENT: frozenset({"name", "student_number", "class_source_id", "status"}),
    EntityType.CLASS: frozenset({"name", "grade", "school_year", "parent_source_id", "status"}),
    EntityType.ORGANIZATION_UNIT: frozenset({"name", "code", "parent_source_id", "status"}),
    EntityType.MEMBERSHIP: frozenset({"member_source_id", "container_source_id", "role", "status"}),
}
```

- [ ] **Step 4: Build operations from persisted facts, not prose**

```python
async def build(self, task_id: UUID, selected: Sequence[ProposalVersionRef]) -> GovernancePlan:
    proposals = await self.proposals.get_exact_pending_versions(task_id, selected)
    contexts = [await self.eligibility.require_current_context(proposal) for proposal in proposals]
    operations = tuple(self._operation_from(proposal, context) for proposal, context in zip(proposals, contexts, strict=True))
    self.validator.validate(operations)
    payload = canonical_json([op.model_dump(mode="json") for op in operations])
    return await self.plans.save(task_id, operations, content_hash=sha256(payload).hexdigest())
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/unit/governance/test_plan_builder.py -q`

Expected: AI/manual proposal mapping, stale and superseded versions, mandatory-analysis gate, cross-task, unresolved manual review, disallowed action, and allowed-field tests PASS.

```bash
git add backend/app/governance backend/tests/unit/governance/test_plan_builder.py
git commit -m "feat: build policy-valid governance plans"
```

### Task 3: Add risk policy and dependency ordering

**Files:**
- Create: `backend/app/governance/risk_policy.py`
- Create: `backend/app/governance/dependency_graph.py`
- Test: `backend/tests/unit/governance/test_dependency_graph.py`
- Test: `backend/tests/unit/governance/test_risk_policy.py`

**Interfaces:**
- Consumes: operation type, entity type, affected fields, descendants, and references.
- Produces: authoritative backend risk, reversibility metadata, topological execution order, cycle/conflict errors.

- [ ] **Step 1: Write parent-before-child and risk tests**

```python
def test_parent_create_precedes_class_create(graph, department_create, class_create) -> None:
    class_create = class_create.model_copy(update={"dependencies": {department_create.id}})
    assert graph.order([class_create, department_create]) == [department_create, class_create]

def test_disable_with_dependents_is_high_risk(risk_policy, disable_teacher) -> None:
    result = risk_policy.evaluate(disable_teacher, dependent_count=3)
    assert result.level is RiskLevel.HIGH
    assert result.requires_explicit_confirmation is True

def test_cycle_blocks_plan(graph, cyclic_operations) -> None:
    with pytest.raises(DependencyCycleError):
        graph.order(cyclic_operations)
```

- [ ] **Step 2: Run policy tests**

Run: `cd backend && uv run pytest tests/unit/governance/test_dependency_graph.py tests/unit/governance/test_risk_policy.py -q`

Expected: FAIL because graph and policy are missing.

- [ ] **Step 3: Implement deterministic risk calculation**

```python
def evaluate(operation: GovernanceOperation, dependent_count: int) -> RiskAssessment:
    if operation.operation_type in {OperationType.DISABLE, OperationType.MOVE} or dependent_count > 0:
        return RiskAssessment(level=RiskLevel.HIGH, requires_explicit_confirmation=True)
    if operation.operation_type in {OperationType.CREATE, OperationType.UPDATE}:
        return RiskAssessment(level=RiskLevel.MEDIUM, requires_explicit_confirmation=False)
    return RiskAssessment(level=RiskLevel.LOW, requires_explicit_confirmation=False)
```

- [ ] **Step 4: Implement stable Kahn topological ordering**

```python
def order(operations: Sequence[GovernanceOperation]) -> list[GovernanceOperation]:
    by_id = {op.id: op for op in operations}
    incoming = {op.id: set(op.dependencies) for op in operations}
    ready = sorted((op_id for op_id, deps in incoming.items() if not deps), key=str)
    result = []
    while ready:
        current = ready.pop(0); result.append(by_id[current])
        for op_id in sorted(incoming, key=str):
            if current in incoming[op_id]:
                incoming[op_id].remove(current)
                if not incoming[op_id] and by_id[op_id] not in result and op_id not in ready:
                    ready.append(op_id); ready.sort(key=str)
    if len(result) != len(operations):
        raise DependencyCycleError()
    return result
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/unit/governance/test_dependency_graph.py tests/unit/governance/test_risk_policy.py -q`

Expected: ordering, missing dependencies, cycles, risk, and reversibility tests PASS.

```bash
git add backend/app/governance/risk_policy.py backend/app/governance/dependency_graph.py backend/tests/unit/governance
git commit -m "feat: order and assess governance operations"
```

### Task 4: Expose preview, optional explanation, confirmation, and preflight

**Files:**
- Create: `backend/app/executions/preflight.py`
- Create: `backend/app/governance/plan_explainer.py`
- Create: `backend/app/api/routes/execution_batches.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/api/test_execution_preview.py`

**Interfaces:**
- Consumes: selected proposal/version refs, `Idempotency-Key`, confirmation plan version, high-risk acknowledgement, backend operator.
- Produces: deterministic preview counts/before/after, optional tokenized model explanation, stored plan, `PreflightResult`, confirmed batch or 409 conflicts.

- [ ] **Step 1: Write drift and operator tests**

```python
def test_preview_counts_exact_operations(client, selected_refs) -> None:
    body = client.post("/api/execution-batches/preview", json={"task_id": TASK_ID, "differences": selected_refs}).json()
    assert body["counts"] == {"create": 1, "update": 2, "move": 1, "disable": 0, "skip": 0}
    assert body["proposal_sources"] == {"ai": 2, "operator": 2}

def test_client_operator_id_is_rejected(client, valid_confirmation) -> None:
    response = client.post("/api/execution-batches", json={**valid_confirmation, "operator_id": "spoofed"})
    assert response.status_code == 422

def test_changed_before_value_returns_conflict(client, drifted_confirmation) -> None:
    response = client.post("/api/execution-batches", json=drifted_confirmation)
    assert response.status_code == 409
    assert response.json()["conflicts"][0]["code"] == "before_value_drift"

def test_explanation_failure_does_not_block_confirmation(client, preview, failing_model) -> None:
    explanation = client.post(f"/api/governance-plans/{preview['plan_id']}/explanation")
    assert explanation.status_code == 503
    confirmed = client.post("/api/execution-batches", json=preview["confirmation"])
    assert confirmed.status_code == 202
```

- [ ] **Step 2: Run preview/preflight tests**

Run: `cd backend && uv run pytest tests/integration/api/test_execution_preview.py -q`

Expected: FAIL because endpoints and preflight are absent.

- [ ] **Step 3: Implement preflight checks as explicit results**

```python
class PreflightConflict(BaseModel):
    operation_id: UUID
    code: Literal["proposal_version_drift", "difference_version_drift", "target_version_drift", "before_value_drift", "dependency_missing", "mapping_conflict", "ineligible"]
    message: str

class PreflightResult(BaseModel):
    plan_id: UUID
    plan_version: int
    target_version: str
    conflicts: tuple[PreflightConflict, ...]
    valid: bool

async def check(self, plan: GovernancePlan) -> PreflightResult:
    current_version = await self.target.version()
    conflicts = await self._version_and_values(plan, current_version)
    conflicts += self._dependencies_and_eligibility(plan)
    return PreflightResult(plan_id=plan.id, plan_version=plan.version, target_version=current_version.value,
                           conflicts=tuple(conflicts), valid=not conflicts)
```

- [ ] **Step 4: Bind confirmation to backend identity and exact plan version**

```python
class ConfirmBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: UUID
    plan_version: int
    high_risk_acknowledged: bool = False

@router.post("/execution-batches", status_code=202)
async def confirm_batch(body: ConfirmBatchRequest, operator=Depends(current_operator), service=Depends(get_execution_service)):
    return await service.confirm(body, confirmed_by=operator.id, independent_reviewer_id=None)
```

- [ ] **Step 5: Add optional shared-model plan explanation**

Use the existing `HttpLLMProvider`, `TaskTokenizationContext`, and `generate-governance-plan` Skill with a plan-explanation schema containing only summary, risk explanation, and attention points. The Agent receives read-only proposal and execution context and cannot return operations. Persist safe provenance separately from the immutable deterministic plan. A provider or validation failure returns an unavailable explanation state and never changes plan validity.

- [ ] **Step 6: Verify and commit**

Run: `cd backend && uv run pytest tests/integration/api/test_execution_preview.py -q`

Expected: exact proposal/source counts, stale proposal/plan/target detection, before-value drift, conflict, high-risk acknowledgement, non-blocking explanation failure, idempotency, and spoofed identity tests PASS.

```bash
git add backend/app/executions/preflight.py backend/app/api/routes/execution_batches.py backend/app/main.py backend/tests/integration/api/test_execution_preview.py
git commit -m "feat: preview and preflight execution batches"
```

### Task 5: Derive immutable Seewo CSV versions

**Files:**
- Create: `backend/app/executions/csv_versioning.py`
- Modify: `backend/app/connectors/csv_target.py`
- Test: `backend/tests/integration/executions/test_csv_versioning.py`

**Interfaces:**
- Consumes: parent target version, ordered operations, CSV mapping, output storage root.
- Produces: `TargetMutationSession` and exactly one new `TargetVersion(parent_id, sha256, path, batch_id)` per batch; preserves unknown columns and neutralizes formula injection on export.

- [ ] **Step 1: Test version derivation and original preservation**

```python
async def test_apply_creates_child_and_preserves_original(versioner, uploaded_target, update_operation) -> None:
    original = uploaded_target.path.read_bytes()
    child = await versioner.apply(uploaded_target, [update_operation], batch_id=BATCH_ID)
    assert child.parent_id == uploaded_target.id
    assert child.path != uploaded_target.path
    assert uploaded_target.path.read_bytes() == original

async def test_export_neutralizes_formula_values(versioner, create_operation) -> None:
    create_operation.after["name"] = "=HYPERLINK(\"bad\")"
    child = await versioner.apply(empty_target(), [create_operation], batch_id=BATCH_ID)
    assert "'=HYPERLINK" in child.path.read_text()
```

- [ ] **Step 2: Run CSV version tests**

Run: `cd backend && uv run pytest tests/integration/executions/test_csv_versioning.py -q`

Expected: FAIL because CSV versioning is absent.

- [ ] **Step 3: Implement pure row operations and safe serialization**

```python
def safe_csv_value(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value

def apply_operation(rows: list[dict], operation: GovernanceOperation) -> list[dict]:
    if operation.operation_type is OperationType.CREATE:
        return [*rows, dict(operation.after or {})]
    index = unique_target_index(rows, operation.target_entity_id)
    if operation.restore_absence:
        if operation.compensation_for is None:
            raise PlanPolicyError("restore_absence is compensation-only")
        return [*rows[:index], *rows[index + 1:]]
    updated = dict(rows[index])
    if operation.operation_type in {OperationType.UPDATE, OperationType.MOVE, OperationType.DISABLE}:
        updated.update(operation.after or {})
    return [*rows[:index], updated, *rows[index + 1:]]
```

```python
class TargetMutationSession(Protocol):
    async def apply_operation(self, operation: GovernanceOperation) -> None: ...
    async def read_entity(self, entity_id: UUID | str) -> dict[str, Any] | None: ...
    async def finalize(self) -> TargetVersion: ...
    async def abort(self) -> None: ...

class BatchTargetConnector(TargetConnector, Protocol):
    async def begin_batch(self, parent_version_id: UUID, batch_id: UUID) -> TargetMutationSession: ...
```

- [ ] **Step 4: Write child atomically and store hash**

```python
async def derive(self, parent: TargetVersion, operations, batch_id) -> TargetVersion:
    frame = pl.read_csv(parent.path, infer_schema_length=0)
    rows = frame.to_dicts()
    for operation in operations:
        rows = apply_operation(rows, operation)
    temp = self.root / f".{uuid4().hex}.tmp"
    output = self.root / f"{uuid4().hex}.csv"
    pl.DataFrame([{k: safe_csv_value(v) for k, v in row.items()} for row in rows]).write_csv(temp)
    temp.replace(output)
    return await self.versions.create(parent.id, output, sha256_file(output), batch_id)
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/integration/executions/test_csv_versioning.py -q`

Expected: create/update/move/disable, extra-column preservation, safe formula values, unique target, atomic write, parent link, and original preservation tests PASS.

```bash
git add backend/app/executions/csv_versioning.py backend/app/connectors/csv_target.py backend/tests/integration/executions/test_csv_versioning.py
git commit -m "feat: derive immutable seewo csv versions"
```

### Task 6: Execute, verify, partially fail, and retry

**Files:**
- Create: `backend/app/executions/verifier.py`
- Create: `backend/app/executions/executor.py`
- Test: `backend/tests/integration/executions/test_executor.py`

**Interfaces:**
- Consumes: confirmed batch, preflight target version, ordered operations, batch-capable target connector.
- Produces: per-operation attempts, derived target chain, final `SUCCEEDED`, `PARTIAL_FAILURE`, or `FAILED` batch, retryable operation IDs.

- [ ] **Step 1: Write partial failure and verification tests**

```python
async def test_unrelated_operation_continues_after_failure(executor, batch, target_stub) -> None:
    target_stub.fail_operation(batch.operations[1].id, RetryableConnectorError("timeout"))
    result = await executor.execute(batch.id)
    assert result.status == "partial_failure"
    assert result.operations[0].status == "succeeded"
    assert result.operations[1].retryable is True
    assert result.operations[2].status == "succeeded"

async def test_success_response_with_wrong_state_is_verification_failed(executor, batch, target_stub) -> None:
    target_stub.return_wrong_value(batch.operations[0].id)
    result = await executor.execute(batch.id)
    assert result.operations[0].status == "verification_failed"
```

- [ ] **Step 2: Run executor tests**

Run: `cd backend && uv run pytest tests/integration/executions/test_executor.py -q`

Expected: FAIL because executor/verifier are missing.

- [ ] **Step 3: Implement expected-versus-actual verification**

```python
class TargetVerifier:
    async def verify(self, session: TargetMutationSession, operation: GovernanceOperation) -> VerificationResult:
        actual = await session.read_entity(operation.target_entity_id or operation.after["source_id"])
        expected = operation.after
        mismatches = {field: {"expected": expected.get(field), "actual": actual.get(field)}
                      for field in operation.changed_fields if expected.get(field) != actual.get(field)}
        return VerificationResult(valid=not mismatches, actual=actual, mismatches=mismatches)
```

- [ ] **Step 4: Implement independent attempts and dependency blocking**

```python
async def execute(self, batch_id: UUID, retry_only: frozenset[UUID] | None = None) -> ExecutionBatchResult:
    batch = await self.repository.get_confirmed(batch_id)
    session = await self.target.begin_batch(batch.input_version_id, batch.id)
    for operation in self.graph.order(batch.operations):
        if retry_only is not None and operation.id not in retry_only:
            continue
        if await self.repository.has_failed_dependency(operation):
            await self.repository.append_attempt(operation.id, status="blocked", error_code="dependency_failed")
            continue
        try:
            await session.apply_operation(operation)
            verification = await self.verifier.verify(session, operation)
            status = "succeeded" if verification.valid else "verification_failed"
            await self.repository.append_attempt(operation.id, status=status, actual_after=verification.actual, verification=verification.model_dump())
        except ConnectorError as error:
            await self.repository.append_attempt(operation.id, status="failed", error_code=error.code, retryable=error.retryable)
    output_version = await session.finalize()
    await self.repository.bind_output_version(batch.id, output_version.id)
    return await self.repository.finalize_batch(batch_id)
```

- [ ] **Step 5: Add eligible retry behavior and commit**

Run: `cd backend && uv run pytest tests/integration/executions/test_executor.py -q`

Expected: order, idempotency, dependency blocking, partial failure, retry filtering, verification failure, and audit tests PASS.

```bash
git add backend/app/executions/verifier.py backend/app/executions/executor.py backend/tests/integration/executions/test_executor.py
git commit -m "feat: execute and verify governance batches"
```

### Task 7: Expose execution monitoring, retry, history, and downloads

**Files:**
- Modify: `backend/app/api/routes/execution_batches.py`
- Create: `backend/app/api/routes/execution_records.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/api/test_execution_records.py`

**Interfaces:**
- Consumes: batch ID, retry command, history filters for task/operator/date/status/rollback state, cursor.
- Produces: execute/retry endpoints, stable history/detail APIs, immutable audit timeline, derived CSV download.

- [ ] **Step 1: Write history, retry, and identity tests**

```python
def test_history_returns_backend_actor_and_attempts(client, completed_batch) -> None:
    body = client.get(f"/api/execution-records/{completed_batch.id}").json()
    assert body["operator_id"] == "demo-operator"
    assert body["operations"][0]["attempts"]
    assert body["source_snapshot_id"] and body["target_snapshot_id"]

def test_retry_rejects_non_retryable_operation(client, failed_batch) -> None:
    response = client.post(f"/api/execution-batches/{failed_batch.id}/retry", json={"operation_ids": [str(failed_batch.non_retryable_id)]})
    assert response.status_code == 409
```

- [ ] **Step 2: Run history API tests**

Run: `cd backend && uv run pytest tests/integration/api/test_execution_records.py -q`

Expected: FAIL because routes are missing.

- [ ] **Step 3: Implement stable list/detail and permitted actions**

```python
@router.get("/execution-records", response_model=ExecutionRecordPage)
async def list_records(filters: Annotated[ExecutionHistoryFilters, Query()], repo=Depends(get_execution_repo)):
    return await repo.list_history(filters)

@router.get("/execution-records/{batch_id}", response_model=ExecutionRecordDetail)
async def get_record(batch_id: UUID, repo=Depends(get_execution_repo)):
    return await repo.get_detail_or_404(batch_id)

@router.post("/execution-batches/{batch_id}/retry", status_code=202)
async def retry(batch_id: UUID, body: RetryRequest, operator=Depends(current_operator), service=Depends(get_execution_service)):
    return await service.retry(batch_id, body.operation_ids, operator.id)
```

- [ ] **Step 4: Add controlled file download**

```python
@router.get("/execution-records/{batch_id}/target-version")
async def download_target(batch_id: UUID, service=Depends(get_execution_service)):
    version = await service.downloadable_version(batch_id)
    return FileResponse(version.path, media_type="text/csv", filename=f"seewo-{version.id}.csv")
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/integration/api/test_execution_records.py tests/integration/executions -q`

Expected: pagination, filters, immutable before/after, errors, retries, verification, actor identity, permitted actions, and download tests PASS.

```bash
git add backend/app/api/routes/execution_batches.py backend/app/api/routes/execution_records.py backend/app/main.py backend/tests/integration/api/test_execution_records.py
git commit -m "feat: expose execution audit and retries"
```

### Task 8: Add full execution acceptance coverage

**Files:**
- Create: `backend/tests/e2e/test_governance_execution.py`
- Create: `backend/tests/fixtures/execution_cases.py`

**Interfaces:**
- Consumes: synthetic task with AI and operator-authored `pending_execution` proposals from modules 1-4 plus `ai-new-ui`.
- Produces: regression proof from proposal selection and batch review through verified derived CSV and immutable history.

- [ ] **Step 1: Write the vertical-slice test**

```python
async def test_reviewed_proposals_produce_verified_csv(app_client, complete_proposed_task) -> None:
    preview = await app_client.post("/api/execution-batches/preview", json=complete_proposed_task.selection)
    confirmed = await app_client.post("/api/execution-batches", json={
        "plan_id": preview.json()["plan_id"], "plan_version": preview.json()["plan_version"],
        "high_risk_acknowledged": True,
    }, headers={"Idempotency-Key": "e2e-execution-1"})
    result = await run_batch(confirmed.json()["id"])
    assert result.status == "succeeded"
    downloaded = await app_client.get(f"/api/execution-records/{result.id}/target-version")
    assert downloaded.status_code == 200
    assert complete_proposed_task.original_target.read_bytes() == complete_proposed_task.original_bytes
```

- [ ] **Step 2: Run all execution tests**

Run: `cd backend && uv run pytest tests/unit/governance tests/integration/executions tests/integration/api/test_execution_preview.py tests/integration/api/test_execution_records.py tests/e2e/test_governance_execution.py -q`

Expected: policy, drift, dependency, partial failure, retry, verification, audit identity, download, and original-preservation cases PASS.

- [ ] **Step 3: Commit acceptance coverage**

```bash
git add backend/tests/e2e/test_governance_execution.py backend/tests/fixtures/execution_cases.py
git commit -m "test: verify versioned governance execution"
```

## Module Acceptance

Run: `cd backend && uv run pytest tests/unit/governance tests/integration/executions tests/integration/api/test_execution_preview.py tests/integration/api/test_execution_records.py tests/e2e/test_governance_execution.py -q && uv run ruff check . && uv run mypy app`

Expected: only exact current reviewed proposal versions execute; AI and manual proposals share one deterministic policy path; unresolved manual review is excluded; optional LLM explanation cannot block execution; identity spoofing is impossible; all mutations produce derived CSV versions; partial failure and retries retain append-only facts; verification failures never appear successful.

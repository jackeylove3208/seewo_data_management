# Mixed Database Schema Mapping Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make explicit-source/LLM-target schema mapping reliable while preserving AI field discovery, actionable repair feedback, and lease fencing during slow model work.

**Architecture:** Partition database roles by configured mapping mode, compile explicit roles on the server, send only LLM-role profiles to the model, then merge and validate a complete frozen mapping before caching. Represent safe domain validation failures as path/code feedback. Renew a late heartbeat when the same owner/token still holds the row lock, while distinguishing run-claim loss from school-lock loss.

**Tech Stack:** Python 3.12, FastAPI domain services, SQLAlchemy async sessions, Pydantic v2, pytest, Ruff, mypy.

## Global Constraints

- Keep `seewo-data-mysql` in `mapping.mode: llm`; do not hard-code its business-field mapping in YAML.
- Never expose raw database rows, credentials, DSNs, arbitrary SQL, or generic database tools to the model.
- Preserve the exact six fields `category`, `name`, `number`, `class_name`, `phone`, and `email`.
- Preserve historical frozen source bindings and successful mapping checkpoints.
- Do not weaken worker/token/attempt/cursor fencing at commit boundaries.
- Use synthetic organization data only in tests.

---

### Task 1: Mixed explicit and LLM role mapping

**Files:**
- Modify: `backend/app/agent_graph/production_executor.py:1267-1383`
- Modify: `backend/app/agent_graph/production_executor.py:4235-4350`
- Test: `backend/tests/integration/agent_graph/test_production_runtime.py`

**Interfaces:**
- Consumes: `_DatabaseMappingMaterials.mapping_modes`, `.profiles`, `.configured_mappings`, `.field_refs`, and `_validate_database_mapping_output(...)`.
- Produces: `_merge_database_mapping_output(candidate, explicit_output, llm_roles) -> DatabaseSchemaMappingOutput` and model requests whose `DatabaseSchemaMappingInput.sources` include only LLM roles.

- [ ] **Step 1: Write failing mixed-mode tests**

Add connectors with an explicit authoritative mapping and an LLM target. Script the provider to return an empty `authoritative_mappings` array and a valid target mapping, then assert:

```python
prompt = "\n".join(message.content for message in provider.requests[0].messages)
assert "database-column:authoritative:" not in prompt
assert "database-column:target:" in prompt
assert checkpoints["authoritative"].payload["mapping"]["number"] == "number"
assert checkpoints["target"].payload["mapping"]["number"] == "number"
```

Add the inverse LLM-source/explicit-target case and retain existing LLM/LLM and explicit/explicit coverage.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest \
  tests/integration/agent_graph/test_production_runtime.py \
  -k 'mixed_mapping' -q
```

Expected: FAIL because the current request includes both database roles and requires the model to reproduce the explicit mapping.

- [ ] **Step 3: Implement role partitioning and server merge**

In `_resolve_database_mapping_output`, derive LLM roles from `materials.mapping_modes`, build an explicit output from frozen configuration, and send only LLM profiles:

```python
llm_roles = tuple(
    role
    for role in ("authoritative", "target")
    if materials.mapping_modes.get(role) == "llm"
)
explicit_output = _database_mapping_output_from_config(
    configured_mappings=materials.configured_mappings,
    field_refs=materials.field_refs,
    schema_version=mapping_schema_version,
)
```

If `llm_roles` is empty, validate and return `explicit_output` without a model call. Otherwise, pass only profiles whose `source_role` is in `llm_roles`. In the result validator, reject candidate mappings for non-requested roles, merge explicit arrays for explicit roles and candidate arrays for LLM roles, preserve unresolved fields only for LLM roles, then call `_validate_database_mapping_output` on the merged result.

- [ ] **Step 4: Run focused mapping tests and verify GREEN**

Run:

```bash
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest \
  tests/integration/agent_graph/test_production_runtime.py \
  -k 'sql_v3 or mixed_mapping' -q
```

Expected: all selected tests PASS, with one model call for a new LLM schema and zero calls on cache reuse.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/agent_graph/production_executor.py \
  backend/tests/integration/agent_graph/test_production_runtime.py
git commit -m "fix: isolate llm database mapping roles"
```

### Task 2: Actionable and durable mapping repair feedback

**Files:**
- Modify: `backend/app/agent_graph/production_executor.py:4102-4162`
- Modify: `backend/app/agent_graph/production_executor.py:4266-4350`
- Modify: `backend/app/agent_graph/repository.py:578-590`
- Test: `backend/tests/integration/agent_graph/test_production_runtime.py`
- Test: `backend/tests/integration/agent_graph/test_real_subagents.py`

**Interfaces:**
- Produces: `_DatabaseMappingContractViolation(ValueError)` with `repair_feedback: tuple[{"path": str, "code": str}, ...]`.
- Consumes: `GraphSkillModelRunner` support for an exception-owned `repair_feedback` attribute and `AgentGraphRepository.prepare_invocation_resume(...)`.

- [ ] **Step 1: Write failing safe-feedback tests**

Add a provider whose first database mapping uses the target primary-key ref for `number` and whose second output is valid. Assert the second request contains:

```python
assert '"code": "primary_or_version_field_forbidden"' in repair_message
assert '"path": "target_mappings.number"' in repair_message
```

Add an interruption/resume test that persists `{"path": ..., "code": ...}` feedback and confirms the recovered request still receives the same code.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest \
  tests/integration/agent_graph/test_production_runtime.py \
  tests/integration/agent_graph/test_real_subagents.py \
  -k 'mapping_repair_code or code_feedback_survives' -q
```

Expected: FAIL because mapping validators currently raise generic `ValueError` and persisted feedback accepts only `type`.

- [ ] **Step 3: Implement stable domain feedback**

Add a private exception with sanitized feedback:

```python
class _DatabaseMappingContractViolation(ValueError):
    def __init__(self, code: str, *, path: str = "$") -> None:
        super().__init__("database mapping violated its fixed contract")
        self.repair_feedback = ({"path": path, "code": code},)
```

Replace database mapping validator `ValueError` branches with stable codes such as `role_not_requested`, `source_field_unknown`, `primary_or_version_field_forbidden`, `normalizer_invalid`, `entity_kinds_invalid`, and `fixed_field_coverage_incomplete`. Update `_safe_repair_feedback` to accept either a `code` or legacy `type` key and preserve the original key.

- [ ] **Step 4: Run feedback tests and verify GREEN**

Run the RED command again. Expected: selected tests PASS and no raw schema values or model output appear in feedback.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/app/agent_graph/production_executor.py \
  backend/app/agent_graph/repository.py \
  backend/tests/integration/agent_graph/test_production_runtime.py \
  backend/tests/integration/agent_graph/test_real_subagents.py
git commit -m "fix: return actionable schema mapping feedback"
```

### Task 3: Late heartbeat renewal and loss diagnostics

**Files:**
- Modify: `backend/app/agent_runtime/repository.py:33-47`
- Modify: `backend/app/agent_runtime/repository.py:399-416`
- Modify: `backend/app/agent_graph/worker.py:29-32`
- Modify: `backend/app/agent_graph/worker.py:119-141`
- Modify: `backend/app/agent_graph/worker.py:422-450`
- Test: `backend/tests/integration/repositories/test_agent_runtime.py`
- Test: `backend/tests/integration/agent_graph/test_worker.py`

**Interfaces:**
- Produces: `AgentGraphLeaseLost.reason` with `run_claim_lost` or `school_lock_lost`.
- Changes: `heartbeat_run_claim(...)` renews an expired timestamp only when owner/token still match under the row lock.

- [ ] **Step 1: Write failing lease tests**

Change the existing expiry test to assert a same-owner/token late heartbeat renews successfully before another claimant runs. Add a separate sequence in which worker 2 reclaims first, then assert worker 1 heartbeat fails. Add a graph-worker test that deactivates the school lock and asserts:

```python
with pytest.raises(AgentGraphLeaseLost) as caught:
    await worker_task
assert caught.value.reason == "school_lock_lost"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest \
  tests/integration/repositories/test_agent_runtime.py \
  tests/integration/agent_graph/test_worker.py \
  -k 'expired_run_lease or school_lock_loss' -q
```

Expected: FAIL because an expired timestamp currently rejects the same owner and heartbeat returns only a boolean.

- [ ] **Step 3: Implement safe late renewal and typed reason**

Inside `heartbeat_run_claim`, keep `SELECT ... FOR UPDATE` and validate owner/token without requiring an unexpired timestamp. Do not change `claim_next_run`; another worker may still reclaim an expired row first. Give `AgentGraphLeaseLost` a sanitized `reason`, and return a distinct heartbeat failure reason when the run claim or school lock is lost.

- [ ] **Step 4: Run lease tests and verify GREEN**

Run the RED command again. Expected: late same-owner renewal PASS, post-reclaim fencing PASS, and school-lock diagnostic PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add backend/app/agent_runtime/repository.py backend/app/agent_graph/worker.py \
  backend/tests/integration/repositories/test_agent_runtime.py \
  backend/tests/integration/agent_graph/test_worker.py
git commit -m "fix: renew owned graph leases safely"
```

### Task 4: Integration verification and delivery gates

**Files:**
- Modify only if verification reveals a scoped defect in files from Tasks 1-3.
- Verify: backend mapping, graph worker, repository, type, lint, migration, and OpenSpec suites.

**Interfaces:**
- Consumes all behavior from Tasks 1-3.
- Produces no new runtime interface.

- [ ] **Step 1: Run focused regression files**

```bash
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest \
  tests/integration/agent_graph/test_production_runtime.py \
  tests/integration/agent_graph/test_real_subagents.py \
  tests/integration/agent_graph/test_worker.py \
  tests/integration/repositories/test_agent_runtime.py -q
```

Expected: all tests PASS.

- [ ] **Step 2: Run backend quality gates**

```bash
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/ruff check .
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/mypy app
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest
```

Expected: all commands exit 0.

- [ ] **Step 3: Run migration smoke test**

```bash
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  /Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest \
  tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

Expected: one test PASS and only `reconcile_migration_test` is recreated.

- [ ] **Step 4: Run strict OpenSpec validation**

```bash
openspec validate --all --strict --no-interactive
```

Expected: validation exits 0.

- [ ] **Step 5: Review the final diff and commit any verification-only correction**

```bash
git diff --check
git status --short
git log --oneline --decorate -5
```

Expected: no unstaged implementation changes and no unrelated files.

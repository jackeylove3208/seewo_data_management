# Resumable Agent Tool Investigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve completed Agent tool investigation across the existing four semantic model attempts so provider, contract, or worker failures resume from the last safe tool checkpoint instead of starting over.

**Architecture:** Extend existing sub-agent tool-call audit rows with replay-safe descriptors and model-turn positions. The graph runner reconstructs prior tool messages by re-executing authorized read-only tools and checking result hashes, while the analysis path commits invocation and tool checkpoints through short transactions between model requests. Existing four-attempt exhaustion and safety boundaries remain unchanged.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async ORM, Alembic, Pydantic v2, PostgreSQL/SQLite test database, pytest, React/TypeScript presentation tests.

## Global Constraints

- Keep the existing maximum of four semantic attempts.
- Preserve model-directed step-by-step tool investigation.
- Do not persist complete prompts, raw model responses, credentials, or raw tool-result payloads.
- Persist only allowlisted replay descriptors, hashes, trace metadata, and safe validation feedback.
- Reauthorize every replayed tool and fail closed when its reconstructed result hash changes.
- Keep tenant, evidence-manifest, worker-lease, graph-cursor, privacy, and school-lock boundaries intact.
- Do not change reconciliation decisions, risk policy, approval policy, or execution behavior.

---

### Task 1: Persist replay-safe tool checkpoint metadata

**Files:**
- Create: `backend/alembic/versions/0044_resumable_agent_tool_calls.py`
- Modify: `backend/app/models/agent_graph.py:143-203`
- Modify: `backend/app/agent_graph/repository.py:295-430`
- Test: `backend/tests/integration/agent_graph/test_repository.py`
- Test: `backend/tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: existing `AgentSubAgentInvocationRecord`, `AgentToolCallRecord`, and `AgentGraphRepository.record_tool_call`.
- Produces: nullable `model_turn: int | None`, nullable `replay_descriptor: dict[str, Any] | None`, extended `record_tool_call(...)`, and `list_replayable_tool_calls(...) -> tuple[AgentToolCallRecord, ...]`.

- [ ] **Step 1: Write failing repository tests for replay metadata**

Add tests that record two authorized completed calls across attempts and assert ordered retrieval excludes denied, failed, descriptor-less, different-input, and different-action rows:

```python
calls = await repository.list_replayable_tool_calls(
    graph_run_id=state.id,
    cursor=state.cursor,
    action_id="analyze_batch_1",
    skill_name="reconcile-entity-batch",
    input_hash="sha256:input",
)
assert [(item.model_turn, item.tool_name) for item in calls] == [
    (1, "read_work_item"),
    (2, "read_claim_state"),
]
assert calls[0].replay_descriptor == {
    "resource_id": "work-item:00000000-0000-0000-0000-000000000001"
}
```

Also extend the model metadata test to assert the new fields default to `None` for historical rows.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph/test_repository.py -q
```

Expected: FAIL because `record_tool_call` does not accept replay metadata and `list_replayable_tool_calls` does not exist.

- [ ] **Step 3: Add the additive migration and ORM fields**

Create revision `0044_resumable_agent_tool_calls`, revising `0043_superseded_model_batches`, with nullable columns:

```python
def upgrade() -> None:
    with op.batch_alter_table("agent_tool_calls") as batch_op:
        batch_op.add_column(sa.Column("model_turn", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("replay_descriptor", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_tool_calls") as batch_op:
        batch_op.drop_column("replay_descriptor")
        batch_op.drop_column("model_turn")
```

Mirror the columns in `AgentToolCallRecord`:

```python
model_turn: Mapped[int | None] = mapped_column(Integer, nullable=True)
replay_descriptor: Mapped[dict[str, Any] | None] = mapped_column(
    _json_type(), nullable=True
)
```

- [ ] **Step 4: Extend repository write and ordered replay lookup**

Change the write signature to require explicit replay metadata from new callers while keeping safe defaults for existing callers:

```python
async def record_tool_call(
    self,
    *,
    invocation_id: UUID,
    tool_name: str,
    arguments_hash: str,
    result_hash: str,
    authorized: bool,
    status: str,
    trace_id: str,
    model_turn: int | None = None,
    replay_descriptor: dict[str, Any] | None = None,
) -> AgentToolCallRecord:
    ...
```

Add a joined query ordered by invocation attempt and tool sequence:

```python
async def list_replayable_tool_calls(
    self,
    *,
    graph_run_id: UUID,
    cursor: int,
    action_id: str,
    skill_name: str,
    input_hash: str,
) -> tuple[AgentToolCallRecord, ...]:
    statement = (
        select(AgentToolCallRecord)
        .join(AgentSubAgentInvocationRecord)
        .where(
            AgentSubAgentInvocationRecord.graph_run_id == graph_run_id,
            AgentSubAgentInvocationRecord.cursor == cursor,
            AgentSubAgentInvocationRecord.action_id == action_id,
            AgentSubAgentInvocationRecord.skill_name == skill_name,
            AgentSubAgentInvocationRecord.input_hash == input_hash,
            AgentToolCallRecord.authorized.is_(True),
            AgentToolCallRecord.status == "completed",
            AgentToolCallRecord.replay_descriptor.is_not(None),
        )
        .order_by(
            AgentSubAgentInvocationRecord.attempt,
            AgentToolCallRecord.sequence,
        )
    )
    return tuple(await self.session.scalars(statement))
```

- [ ] **Step 5: Run repository and migration tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph/test_repository.py tests/integration/test_migrations.py -q
```

Expected: PASS, with environment-gated PostgreSQL migration tests skipped when their URL is absent.

- [ ] **Step 6: Commit the schema boundary**

```bash
git add backend/alembic/versions/0044_resumable_agent_tool_calls.py backend/app/models/agent_graph.py backend/app/agent_graph/repository.py backend/tests/integration/agent_graph/test_repository.py backend/tests/integration/test_migrations.py
git commit -m "feat: persist replayable agent tool checkpoints"
```

---

### Task 2: Reauthorize and replay completed tools safely

**Files:**
- Modify: `backend/app/agent_graph/tools.py:45-270`
- Modify: `backend/app/ai/graph_subagents.py:60-110`
- Test: `backend/tests/integration/agent_graph/test_real_subagents.py`
- Test: `backend/tests/unit/agent_graph/test_tools.py`

**Interfaces:**
- Consumes: replay metadata from Task 1 and existing `GraphToolContext` authorization.
- Produces: `GraphToolReplayCheckpoint`, `GraphToolReplayConflict`, extended `GraphPhaseToolGateway.call(..., model_turn)`, and `GraphPhaseToolGateway.replay(...)`.

- [ ] **Step 1: Write failing replay tests**

Add tests proving replay reauthorizes and executes a handler without appending a second audit call:

```python
result = await gateway.replay(
    "read_work_item",
    context=context,
    arguments={"resource_id": resource_id},
    expected_result_hash=expected_hash,
)
assert result.payload == expected_payload
persisted_calls = tuple(
    await session.scalars(
        select(AgentToolCallRecord).where(
            AgentToolCallRecord.invocation_id == invocation.id
        )
    )
)
assert len(persisted_calls) == 1
```

Add negative tests for an unauthorized descriptor and a changed result hash:

```python
with pytest.raises(GraphToolReplayConflict):
    await gateway.replay(
        "read_work_item",
        context=context,
        arguments={"resource_id": resource_id},
        expected_result_hash="sha256:stale",
    )
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/agent_graph/test_tools.py tests/integration/agent_graph/test_real_subagents.py -q
```

Expected: FAIL because replay types and gateway methods do not exist.

- [ ] **Step 3: Introduce typed replay state and conflict**

Add:

```python
class GraphToolReplayConflict(RuntimeError):
    """A committed tool checkpoint no longer reconstructs the same evidence."""


@dataclass(frozen=True)
class GraphToolReplayCheckpoint:
    tool_name: str
    arguments: dict[str, Any]
    arguments_hash: str
    result_hash: str
    model_turn: int
```

Validate `model_turn >= 1`, require the descriptor to be a JSON object, and reject descriptors containing keys outside the selected tool schema before constructing this type.

- [ ] **Step 4: Persist allowlisted arguments on normal calls**

Extend `GraphPhaseToolGateway.call` with `model_turn: int | None = None`. After authorization and handler success, write:

```python
await self._repository.record_tool_call(
    invocation_id=context.invocation_id,
    tool_name=tool_name,
    arguments_hash=arguments_hash,
    result_hash=_safe_hash(payload),
    authorized=True,
    status="completed",
    trace_id=trace_id,
    model_turn=model_turn,
    replay_descriptor=dict(arguments),
)
```

Denied and failed calls retain `replay_descriptor=None` so they can never be resumed as completed evidence.

- [ ] **Step 5: Add replay without duplicate audit writes**

Implement replay by running the same durable-context authorization, call authorization, and registered handler, then compare `_safe_hash(payload)` with `expected_result_hash`. Do not call `record_tool_call` during replay. Raise `GraphToolReplayConflict` on mismatch and preserve existing authorization and execution exceptions.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/agent_graph/test_tools.py tests/integration/agent_graph/test_real_subagents.py -q
```

Expected: PASS.

```bash
git add backend/app/agent_graph/tools.py backend/app/ai/graph_subagents.py backend/tests/unit/agent_graph/test_tools.py backend/tests/integration/agent_graph/test_real_subagents.py
git commit -m "feat: replay authorized agent tools safely"
```

---

### Task 3: Resume model conversations across four semantic attempts

**Files:**
- Modify: `backend/app/ai/graph_subagents.py:105-490`
- Modify: `backend/app/agent_graph/repository.py:406-500`
- Test: `backend/tests/integration/agent_graph/test_real_subagents.py`

**Interfaces:**
- Consumes: `list_replayable_tool_calls`, `GraphToolReplayCheckpoint`, and `GraphPhaseToolGateway.replay`.
- Produces: `GraphSkillModelRunner(..., durable_tool_recovery: bool = False)`, replay-aware `_run_attempt`, and unchanged maximum `max_retries=3` plus initial attempt.

- [ ] **Step 1: Change the existing interrupted-after-tool test to require continuation**

Update `test_interrupted_invocation_after_tool_result_preserves_audit_and_resumes` so the recovery provider returns only the final result, not the original tool request again:

```python
recovery_provider = ScriptedProvider([valid_result])
recovered = await GraphSkillModelRunner(
    recovery_session,
    provider=recovery_provider,
    tool_gateway=recovery_gateway,
    operator=operator,
    durable_tool_recovery=True,
).run(request)

assert recovered.attempt_count == 2
assert tool_executions == 2  # original execution plus local replay
assert len(tool_calls) == 1  # replay does not create a second audit fact
assert "authorized_tool_result" in recovery_provider.requests[0].messages[-1].content
```

Use a second database session after the simulated crash so the test proves the checkpoint was committed rather than merely visible in one transaction.

- [ ] **Step 2: Add a final-contract-repair continuation test**

Script attempt 1 as one tool request followed by invalid final output, then attempt 2 as a valid final result. Assert attempt 2 receives the replayed tool result and validation feedback, makes no repeated model-directed tool request, and succeeds within the existing four-attempt budget.

- [ ] **Step 3: Run continuation tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/integration/agent_graph/test_real_subagents.py::test_interrupted_invocation_after_tool_result_preserves_audit_and_resumes \
  tests/integration/agent_graph/test_real_subagents.py::test_contract_failure_after_tool_result_retries_final_output_with_replayed_context -q
```

Expected: FAIL because recovery starts from the initial conversation.

- [ ] **Step 4: Load and validate replay checkpoints before each attempt**

After computing `input_hash`, retrieve prior calls and convert them into typed checkpoints. Revalidate every descriptor with `_tool_arguments_schema` and `_validate_tool_arguments`; an invalid stored descriptor raises `GraphToolReplayConflict` rather than reaching the model.

- [ ] **Step 5: Reconstruct messages in correct order**

Refactor `_run_attempt` to build messages in this order:

```python
messages = _initial_messages(skill, invocation, input_payload, manifest=manifest)
for checkpoint in replay_checkpoints:
    replayed = await self._tool_gateway.replay(
        checkpoint.tool_name,
        context=context,
        arguments=checkpoint.arguments,
        expected_result_hash=checkpoint.result_hash,
    )
    messages.extend(_tool_exchange_messages(checkpoint, replayed))
messages = _append_repair_feedback(messages, repair_feedback)
```

The next provider request therefore sees all completed tool investigation before safe contract feedback. Initialize the tool-call limit with `len(replay_checkpoints)` so recovery cannot bypass `max_tool_calls`.

- [ ] **Step 6: Commit durable boundaries only when enabled**

Add:

```python
async def _commit_recovery_boundary(self) -> None:
    if self._durable_tool_recovery:
        await self._session.commit()
```

Call it after creating an invocation record, after every completed new tool call, and after finalizing each semantic attempt. Existing non-analysis call sites retain current transaction ownership with the default `False`.

Before calling a new tool, calculate `model_turn = len(replay_checkpoints) + tool_calls + 1` and pass it to the gateway. The maximum semantic-attempt loop remains:

```python
total_attempts = self._max_retries + 1  # still four when max_retries == 3
```

- [ ] **Step 7: Preserve exact failure semantics**

Provider failures carry no repair feedback but retain replay checkpoints. Contract failures carry `safe_validation_errors` and retain the same checkpoints. `GraphToolReplayConflict` maps to a dedicated non-retryable `tool_replay_conflict` category. Tool authorization failures remain non-retryable.

- [ ] **Step 8: Run the full graph sub-agent suite and commit**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph/test_real_subagents.py tests/unit/ai/test_graph_subagent_tool_schemas.py -q
```

Expected: PASS, including existing four-attempt exhaustion assertions.

```bash
git add backend/app/ai/graph_subagents.py backend/app/agent_graph/repository.py backend/tests/integration/agent_graph/test_real_subagents.py backend/tests/unit/ai/test_graph_subagent_tool_schemas.py
git commit -m "fix: resume agent investigation after model failure"
```

---

### Task 4: Enable short durable transactions for actionable batch analysis

**Files:**
- Modify: `backend/app/agent_graph/production_executor.py:2235-2360`
- Test: `backend/tests/integration/agent_graph/test_production_runtime.py`
- Test: `backend/tests/integration/agent_graph/test_worker.py`

**Interfaces:**
- Consumes: `GraphSkillModelRunner(..., durable_tool_recovery=True)` from Task 3.
- Produces: analysis-only durable recovery wiring without changing other graph Skills.

- [ ] **Step 1: Write a production-path failure/recovery test**

Use a scripted provider that requests two analysis tools, then raises a provider failure. Reclaim the same graph action under the next semantic attempt and return a valid finding. Assert:

```python
assert provider.model_requests == 4
assert second_attempt_request_contains("authorized_tool_result")
assert persisted_invocations == [(1, "failed"), (2, "completed")]
assert persisted_completed_tool_calls == 2
assert final_batch.status == "completed"
```

The second attempt must not ask the model to select the first two tools again.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph/test_production_runtime.py -k "resumes_completed_tool_investigation" -q
```

Expected: FAIL because `_analyze_batch` owns one long model transaction and does not enable durable recovery.

- [ ] **Step 3: Remove the long transaction around the analysis model loop**

Change only the model-session block in `_analyze_batch` from `async with model_session.begin()` to explicit runner-owned durable boundaries. Keep preparation, claim release, and final batch writes in their existing short transactions.

Extend `_analysis_runtime` with a defaulted flag and pass it into the existing runner construction:

```python
async def _analysis_runtime(
    self,
    session: AsyncSession,
    *,
    context: GraphWorkContext,
    action: AllowedActionV1,
    prepare_sensitive_tokens: bool = True,
    durable_tool_recovery: bool = False,
) -> tuple[GraphAnalysisEvidenceTools, GraphSkillModelRunner, UUID]:
    ...
    runner = GraphSkillModelRunner(
        session,
        provider=self._provider,
        tool_gateway=gateway,
        operator=operator,
        max_retries=self._max_retries,
        durable_tool_recovery=durable_tool_recovery,
    )
```

Pass `durable_tool_recovery=True` only from the model-session call in `_analyze_batch`; the preparation call and every other graph Skill retain `False`.

- [ ] **Step 4: Verify claim release and fencing after failures**

Extend the test to assert the batch claim is released after all four semantic attempts fail, school lock remains held in `blocked_model_error`, and a stale worker cannot commit a replay checkpoint.

- [ ] **Step 5: Run production and worker suites and commit**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph/test_production_runtime.py tests/integration/agent_graph/test_worker.py -q
```

Expected: PASS.

```bash
git add backend/app/agent_graph/production_executor.py backend/tests/integration/agent_graph/test_production_runtime.py backend/tests/integration/agent_graph/test_worker.py
git commit -m "fix: checkpoint actionable analysis tool progress"
```

---

### Task 5: Distinguish mixed model failures and verify delivery gates

**Files:**
- Modify: `frontend/src/features/agent-events/presentation.ts:300-320`
- Test: `frontend/src/features/agent-events/presentation.test.ts`
- Modify: `backend/tests/integration/agent_graph/test_worker.py`

**Interfaces:**
- Consumes: existing `failure_categories`, semantic `attempt_count`, and separate model/tool provenance.
- Produces: accurate mixed-failure copy while retaining the existing blocked-state title and four-attempt count.

- [ ] **Step 1: Write a failing mixed-category presentation test**

Add an event containing both `model_provider_failure` and `model_output_failure` and assert the message does not claim that every attempt was solely a structured-output failure:

```typescript
expect(presentFailure(event).message).toContain("模型网关响应和结构化结果均出现失败")
expect(presentFailure(event).message).toContain("任务数据和学校锁仍被安全保留")
```

- [ ] **Step 2: Run the presentation test and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/features/agent-events/presentation.test.ts
```

Expected: FAIL with the old structure-only message.

- [ ] **Step 3: Add mixed-category precedence**

Check for provider plus contract/output categories before the single-category branches. Keep existing Chinese copy for pure provider and pure output failures and preserve the title `模型分析已暂停`.

- [ ] **Step 4: Run focused backend and frontend checks**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/agent_graph/test_worker.py -q
cd ../frontend
npm test -- --run src/features/agent-events/presentation.test.ts
```

Expected: PASS.

- [ ] **Step 5: Run complete quality gates**

Run:

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
cd ../frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
cd ..
openspec validate --all --strict --no-interactive
```

Expected: all commands exit 0; only documented credential/database-gated tests may skip.

Run the clean PostgreSQL migration smoke test when Docker is available:

```bash
cd backend
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

Expected: PASS.

- [ ] **Step 6: Commit the presentation and final verification changes**

```bash
git add frontend/src/features/agent-events/presentation.ts frontend/src/features/agent-events/presentation.test.ts backend/tests/integration/agent_graph/test_worker.py
git commit -m "fix: explain mixed agent model failures"
```

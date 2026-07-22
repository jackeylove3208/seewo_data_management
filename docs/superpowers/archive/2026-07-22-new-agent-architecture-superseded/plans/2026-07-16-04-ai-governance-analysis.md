# AI Governance Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce mandatory, validated, traceable cause analysis and governance advice for every difference while confining Agent and MCP access to read-only reconciliation context.

**Architecture:** Clear deterministic differences receive template-backed explanations; semantically ambiguous differences are handled by one governance Agent using versioned Skills and registered read-only MCP tools. Provider adapters return structured JSON, Pydantic and policy validation reject unsafe or incomplete output, and persisted provenance prevents silent regeneration. The backend eligibility service, not the Agent, decides whether a difference may enter execution.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, SQLAlchemy 2, HTTPX, Tenacity, official Python MCP SDK (`mcp`), pytest.

## Global Constraints

- Every difference needs cause, evidence summary, recommended action, risk, and confidence before selection.
- Deterministic explanations are preferred for clear cases; LLM is fallback for semantic ambiguity.
- The Agent may read difference context, candidate search, mapping rules, and execution context only.
- No MCP tool in this module mutates CSV, target data, mappings, plans, executions, reports, or rollback state.
- Model output is untrusted until Pydantic and business-policy validation pass.
- Invalid output retries at most twice, then persists failure and routes the item to manual review.
- Persist provider, model, Skill version, prompt version, tool trace IDs, usage, and timestamp.
- Historical analysis is returned exactly as persisted and is never silently regenerated.
- External model APIs may receive current data in this phase; all calls still pass through centralized providers for later redaction.

---

## File Map

- `backend/app/ai/providers/`: LLM and embedding provider adapters with retry/usage metadata.
- `backend/app/ai/skills/`: concise versioned Agent instruction packages.
- `backend/app/ai/mcp/`: read-only server and four authorized tools.
- `backend/app/ai/agent.py`: tool-using analysis orchestrator.
- `backend/app/ai/analysis_service.py`: deterministic-first analysis, validation, persistence, and eligibility.
- `backend/app/schemas/governance.py`, `models/analyses.py`, `repositories/analyses.py`: structured outputs and provenance.

### Task 1: Implement centralized model provider contracts

**Files:**
- Modify: `backend/app/ai/providers/base.py`
- Create: `backend/app/ai/providers/llm.py`
- Create: `backend/app/ai/providers/embeddings.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/ai/test_providers.py`

**Interfaces:**
- Consumes: prompts/text batches, model configuration, timeout, and provider credentials.
- Produces: `LLMProvider.complete_json(request) -> LLMResponse` and module-2-compatible `EmbeddingProvider.embed(texts) -> EmbeddingBatch`.

- [ ] **Step 1: Write timeout, retry, and usage tests**

```python
async def test_llm_retries_transient_failure_then_returns_usage(httpx_mock, provider) -> None:
    httpx_mock.add_response(status_code=503)
    httpx_mock.add_response(json={"output": {"cause": "mapping drift"}, "usage": {"input_tokens": 10, "output_tokens": 4}})
    response = await provider.complete_json(LLMRequest(messages=[Message(role="user", content="analyze")]))
    assert response.output["cause"] == "mapping drift"
    assert response.usage.input_tokens == 10
    assert httpx_mock.get_requests().__len__() == 2

async def test_provider_never_logs_authorization(caplog, provider) -> None:
    await provider.complete_json(valid_request())
    assert "Bearer" not in caplog.text
```

- [ ] **Step 2: Run provider tests**

Run: `cd backend && uv run pytest tests/unit/ai/test_providers.py -q`

Expected: FAIL because provider adapters do not exist.

- [ ] **Step 3: Define provider-neutral request/response types**

```python
class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

class LLMRequest(BaseModel):
    messages: list[Message]
    response_schema: dict = Field(default_factory=dict)
    temperature: float = 0

class LLMResponse(BaseModel):
    output: dict
    provider: str
    model: str
    usage: ModelUsage
    request_id: str | None = None

class LLMProvider(Protocol):
    async def complete_json(self, request: LLMRequest) -> LLMResponse: ...
```

- [ ] **Step 4: Implement bounded retry around HTTPX**

```python
class HttpLLMProvider:
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=0.2, max=2),
           retry=retry_if_exception_type((httpx.TimeoutException, TransientModelError)))
    async def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = await self.client.post(
            self.settings.llm_url,
            headers={"Authorization": f"Bearer {self.settings.llm_api_key.get_secret_value()}"},
            json=self.adapter.to_payload(request, self.settings.llm_model),
            timeout=self.settings.llm_timeout_seconds,
        )
        if response.status_code in {429, 500, 502, 503, 504}:
            raise TransientModelError(response.status_code)
        response.raise_for_status()
        return self.adapter.from_payload(response.json())
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/unit/ai/test_providers.py -q && uv run ruff check app/ai/providers`

Expected: success, timeout, transient retry limit, non-retryable 4xx, usage, and secret-log tests PASS.

```bash
git add backend/app/ai/providers backend/app/core/config.py backend/tests/unit/ai/test_providers.py
git commit -m "feat: add governed model providers"
```

### Task 2: Define analysis schemas and immutable provenance

**Files:**
- Create: `backend/app/schemas/governance.py`
- Create: `backend/app/models/analyses.py`
- Create: `backend/app/repositories/analyses.py`
- Create: `backend/alembic/versions/0005_analysis_results.py`
- Test: `backend/tests/integration/repositories/test_analyses.py`

**Interfaces:**
- Consumes: difference ID/version, structured analysis, model metadata, Skill/prompt versions, and tool traces.
- Produces: `CauseAnalysis`, `AnalysisResult`, immutable `AnalysisRepository.save/get_for_difference`.

- [ ] **Step 1: Write schema and immutable-history tests**

```python
def test_analysis_rejects_confidence_outside_range() -> None:
    with pytest.raises(ValidationError):
        CauseAnalysis(cause="x", evidence_summary="y", recommended_action="update", risk="low", confidence=1.2)

async def test_historical_result_is_not_overwritten(repo, saved_analysis) -> None:
    with pytest.raises(ImmutableRecordError):
        await repo.update_output(saved_analysis.id, {"cause": "new"})
```

- [ ] **Step 2: Run schema/repository tests**

Run: `cd backend && uv run pytest tests/integration/repositories/test_analyses.py -q`

Expected: FAIL because analysis persistence is missing.

- [ ] **Step 3: Define strict analysis outputs**

```python
class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class RecommendedAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    MOVE = "move"
    DISABLE = "disable"
    SKIP = "skip"
    MANUAL_REVIEW = "manual_review"

class CauseAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: str = Field(min_length=3, max_length=1000)
    evidence_summary: str = Field(min_length=3, max_length=2000)
    recommended_action: RecommendedAction
    risk: RiskLevel
    confidence: float = Field(ge=0, le=1)

class AnalysisProvenance(BaseModel):
    provider: str
    model: str
    skill_name: str
    skill_version: str
    prompt_version: str
    tool_trace_ids: tuple[str, ...] = ()
    usage: ModelUsage = ModelUsage()
    generated_at: datetime
```

- [ ] **Step 4: Persist version-bound results and failures**

`analysis_results` stores `difference_id`, `difference_version`, `status`, `output JSONB`, `failure_code`, `attempt_count`, provider/model/Skill/prompt fields, tool traces, usage, and timestamp. Add uniqueness on `(difference_id, difference_version, analysis_version)`.

```python
async def save_success(self, difference, analysis, provenance) -> AnalysisResult:
    record = AnalysisRecord(difference_id=difference.id, difference_version=difference.version,
                            status="succeeded", output=analysis.model_dump(mode="json"),
                            **provenance.model_dump(mode="json"))
    self.session.add(record)
    await self.session.flush()
    return AnalysisResult.model_validate(record)
```

- [ ] **Step 5: Migrate, verify, and commit**

Run: `cd backend && uv run alembic upgrade head && uv run pytest tests/integration/repositories/test_analyses.py -q`

Expected: valid, invalid, immutable, version uniqueness, and failure record tests PASS.

```bash
git add backend/app/schemas/governance.py backend/app/models/analyses.py backend/app/repositories/analyses.py backend/alembic backend/tests/integration/repositories/test_analyses.py
git commit -m "feat: persist structured analysis provenance"
```

### Task 3: Create versioned analysis Skills

**Files:**
- Create: `backend/app/ai/skills/analyze-data-difference/SKILL.md`
- Create: `backend/app/ai/skills/resolve-ambiguous-entity/SKILL.md`
- Create: `backend/app/ai/skills/generate-governance-plan/SKILL.md`
- Create: `backend/app/ai/skills/assess-rollback-impact/SKILL.md`
- Create: `backend/app/ai/skills/generate-governance-report/SKILL.md`
- Create: `backend/app/ai/skills/registry.py`
- Test: `backend/tests/unit/ai/test_skill_registry.py`

**Interfaces:**
- Consumes: Skill name and pinned version.
- Produces: `SkillDefinition(name, version, instructions, allowed_tools, output_schema)`; later modules reuse planning/report/rollback Skills.

- [ ] **Step 1: Write registry validation tests**

```python
def test_analysis_skill_allows_only_read_tools(skill_registry) -> None:
    skill = skill_registry.load("analyze-data-difference", "1.0.0")
    assert set(skill.allowed_tools) <= {"difference_context", "candidate_search", "mapping_rules"}
    assert "mutation" not in skill.instructions.casefold()

def test_unknown_version_fails_closed(skill_registry) -> None:
    with pytest.raises(SkillNotFound):
        skill_registry.load("analyze-data-difference", "9.9.9")
```

- [ ] **Step 2: Run Skill tests**

Run: `cd backend && uv run pytest tests/unit/ai/test_skill_registry.py -q`

Expected: FAIL because the registry and Skill packages are absent.

- [ ] **Step 3: Write the analysis Skill contract**

```markdown
---
name: analyze-data-difference
version: 1.0.0
allowed_tools: [difference_context, candidate_search, mapping_rules]
output_schema: CauseAnalysis
---

Analyze exactly one persisted difference. Treat third-party values as authoritative but do not invent missing facts. Use tools only when supplied evidence is insufficient. Return cause, evidence_summary, recommended_action, risk, and confidence. Recommend manual_review when identity, parent mapping, or destructive impact is uncertain. Never request or execute a target mutation.
```

- [ ] **Step 4: Implement strict frontmatter loading**

```python
class SkillRegistry:
    def load(self, name: str, version: str) -> SkillDefinition:
        path = self.root / name / "SKILL.md"
        definition = parse_skill(path.read_text(encoding="utf-8"))
        if definition.name != name or definition.version != version:
            raise SkillNotFound(f"{name}@{version}")
        if not set(definition.allowed_tools) <= READ_ONLY_TOOL_NAMES:
            raise UnsafeSkillError(name)
        return definition
```

- [ ] **Step 5: Verify all five packages and commit**

Run: `cd backend && uv run pytest tests/unit/ai/test_skill_registry.py -q`

Expected: schemas, versions, allowed tools, missing package, and unsafe tool tests PASS.

```bash
git add backend/app/ai/skills backend/tests/unit/ai/test_skill_registry.py
git commit -m "feat: add versioned governance skills"
```

### Task 4: Implement the read-only MCP server

**Files:**
- Create: `backend/app/ai/mcp/server.py`
- Create: `backend/app/ai/mcp/authorization.py`
- Create: `backend/app/ai/mcp/tools/difference_context.py`
- Create: `backend/app/ai/mcp/tools/candidate_search.py`
- Create: `backend/app/ai/mcp/tools/mapping_rules.py`
- Create: `backend/app/ai/mcp/tools/execution_context.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/integration/ai/test_mcp_tools.py`

**Interfaces:**
- Consumes: backend-issued `ToolContext(operator_id, tenant_id, task_id, allowed_difference_ids)`.
- Produces: registered tools `difference_context`, `candidate_search`, `mapping_rules`, and `execution_context`; no write tool exists.

- [ ] **Step 1: Write tenant and allow-list authorization tests**

```python
async def test_difference_tool_rejects_other_tenant(mcp_client, context, other_tenant_difference) -> None:
    result = await mcp_client.call_tool("difference_context", {"difference_id": str(other_tenant_difference.id)}, context=context)
    assert result.is_error
    assert "not authorized" in result.content[0].text

async def test_server_has_no_mutation_tools(mcp_client) -> None:
    names = {tool.name for tool in await mcp_client.list_tools()}
    assert names == {"difference_context", "candidate_search", "mapping_rules", "execution_context"}
```

- [ ] **Step 2: Run MCP tests**

Run: `cd backend && uv add 'mcp>=1.8,<2' && uv run pytest tests/integration/ai/test_mcp_tools.py -q`

Expected: FAIL because MCP server is absent.

- [ ] **Step 3: Implement context-scoped authorization**

```python
class ToolContext(BaseModel):
    operator_id: str
    tenant_id: str
    task_id: UUID
    allowed_difference_ids: frozenset[UUID] = frozenset()

async def require_difference(context: ToolContext, difference_id: UUID, repo) -> DifferenceDetail:
    if difference_id not in context.allowed_difference_ids:
        raise ToolAuthorizationError("difference not authorized")
    difference = await repo.get(difference_id)
    if difference.task_id != context.task_id or difference.tenant_id != context.tenant_id:
        raise ToolAuthorizationError("difference not authorized")
    return difference
```

- [ ] **Step 4: Register only read-oriented FastMCP tools**

```python
mcp = FastMCP("organization-reconciliation-context")

@mcp.tool()
async def difference_context(difference_id: str, ctx: Context) -> dict:
    tool_context = get_tool_context(ctx)
    return (await require_difference(tool_context, UUID(difference_id), repositories.differences)).model_dump(mode="json")

@mcp.tool()
async def candidate_search(difference_id: str, query: str, top_k: int = 5, ctx: Context = None) -> dict:
    authorized = await require_difference(get_tool_context(ctx), UUID(difference_id), repositories.differences)
    return await candidate_reader.search(authorized, query, min(max(top_k, 1), 10))
```

- [ ] **Step 5: Verify authorization and commit**

Run: `cd backend && uv run pytest tests/integration/ai/test_mcp_tools.py -q`

Expected: allowed reads succeed; cross-task, cross-tenant, unlisted IDs, excessive Top-K, and mutation-name calls fail closed.

```bash
git add backend/app/ai/mcp backend/tests/integration/ai/test_mcp_tools.py
git commit -m "feat: expose read-only reconciliation mcp tools"
```

### Task 5: Build the governed analysis Agent

**Files:**
- Create: `backend/app/ai/agent.py`
- Create: `backend/app/ai/prompting.py`
- Test: `backend/tests/unit/ai/test_agent.py`

**Interfaces:**
- Consumes: `AnalysisRequest`, pinned Skill, LLM provider, MCP tool gateway, maximum 4 tool calls.
- Produces: `AgentResult(output: CauseAnalysis, provenance: AnalysisProvenance)` or typed failure.

- [ ] **Step 1: Write tool-loop and invalid-output tests**

```python
async def test_agent_calls_only_skill_allowed_tool(agent, model_stub) -> None:
    model_stub.queue(tool_call("candidate_search", {"difference_id": DIFF_ID, "query": "张三"}))
    model_stub.queue(valid_analysis_json())
    result = await agent.analyze(request_for(DIFF_ID))
    assert result.output.recommended_action is RecommendedAction.MANUAL_REVIEW
    assert len(result.provenance.tool_trace_ids) == 1

async def test_agent_rejects_unlisted_tool(agent, model_stub) -> None:
    model_stub.queue(tool_call("apply_target_update", {}))
    with pytest.raises(UnsafeToolCall):
        await agent.analyze(request_for(DIFF_ID))
```

- [ ] **Step 2: Run Agent tests**

Run: `cd backend && uv run pytest tests/unit/ai/test_agent.py -q`

Expected: FAIL because Agent is missing.

- [ ] **Step 3: Implement bounded, allow-listed tool orchestration**

```python
class AgentRequest(BaseModel):
    skill_name: str
    skill_version: str
    input_payload: dict
    tool_context: ToolContext | None = None

class GovernanceAgent:
    async def run_structured(self, request: AgentRequest, output_type: type[BaseModel]) -> AgentResult:
        skill = self.skills.load(request.skill_name, request.skill_version)
        messages = build_messages(skill, request.input_payload)
        traces = []
        for _ in range(4):
            response = await self.llm.complete_json(LLMRequest(messages=messages, response_schema=output_type.model_json_schema()))
            if call := parse_tool_call(response.output):
                if call.name not in skill.allowed_tools:
                    raise UnsafeToolCall(call.name)
                if request.tool_context is None:
                    raise UnsafeToolCall(f"{call.name} requires an authorized tool context")
                tool_result, trace_id = await self.tools.call(call, request.tool_context)
                traces.append(trace_id); messages.extend(tool_messages(call, tool_result)); continue
            output = output_type.model_validate(response.output)
            return AgentResult(output=output, provenance=provenance(response, skill, traces))
        raise ToolLimitExceeded(4)

    async def analyze(self, request: AnalysisRequest) -> AgentResult:
        return await self.run_structured(request.to_agent_request(), CauseAnalysis)
```

- [ ] **Step 4: Verify fixed prompt/version provenance and commit**

Run: `cd backend && uv run pytest tests/unit/ai/test_agent.py -q`

Expected: direct output, allowed tool loop, forbidden tool, max tool count, malformed JSON, and provenance tests PASS.

```bash
git add backend/app/ai/agent.py backend/app/ai/prompting.py backend/tests/unit/ai/test_agent.py
git commit -m "feat: add governed reconciliation agent"
```

### Task 6: Implement deterministic-first mandatory analysis

**Files:**
- Create: `backend/app/ai/deterministic_analysis.py`
- Create: `backend/app/ai/analysis_policy.py`
- Create: `backend/app/ai/analysis_service.py`
- Test: `backend/tests/integration/ai/test_analysis_service.py`

**Interfaces:**
- Consumes: persisted difference/version, Agent, repositories, and retry policy.
- Produces: succeeded/failed/manual-review `AnalysisResult`; `is_executable(difference_id, version) -> bool`.

- [ ] **Step 1: Test deterministic path, fallback, and retry exhaustion**

```python
async def test_clear_missing_case_uses_no_llm(service, missing_difference, llm_spy) -> None:
    result = await service.analyze(missing_difference.id)
    assert result.output.cause == "Authoritative entity has no accepted Seewo mapping"
    assert llm_spy.calls == 0

async def test_ambiguous_case_uses_agent(service, ambiguous_difference, agent_spy) -> None:
    await service.analyze(ambiguous_difference.id)
    assert agent_spy.calls == 1

async def test_invalid_output_twice_routes_to_manual_review(service, bad_agent, ambiguous_difference) -> None:
    result = await service.analyze(ambiguous_difference.id)
    assert result.status == "manual_review"
    assert result.attempt_count == 2
```

- [ ] **Step 2: Run analysis service tests**

Run: `cd backend && uv run pytest tests/integration/ai/test_analysis_service.py -q`

Expected: FAIL because mandatory analysis service is missing.

- [ ] **Step 3: Implement deterministic templates and policy validation**

```python
DETERMINISTIC_ANALYSES = {
    DifferenceType.SEEWO_MISSING: CauseAnalysis(cause="Authoritative entity has no accepted Seewo mapping", evidence_summary="No compatible target entity was accepted", recommended_action="create", risk="medium", confidence=1),
    DifferenceType.SEEWO_REDUNDANT: CauseAnalysis(cause="Target entity is unconsumed in a complete scope", evidence_summary="No authoritative entity consumed this target", recommended_action="disable", risk="high", confidence=.95),
}

def validate_action(difference: DifferenceDetail, analysis: CauseAnalysis) -> None:
    allowed = ACTION_POLICY[difference.difference_type]
    if analysis.recommended_action not in allowed:
        raise AnalysisPolicyError(f"{analysis.recommended_action} not allowed for {difference.difference_type}")
```

- [ ] **Step 4: Implement idempotent service with two validation attempts**

```python
async def analyze(self, difference_id: UUID) -> AnalysisResult:
    difference = await self.differences.get(difference_id)
    if existing := await self.analyses.get_for_difference(difference.id, difference.version):
        return existing
    if template := self.deterministic.for_difference(difference):
        return await self.analyses.save_success(difference, template, deterministic_provenance())
    for attempt in range(1, 3):
        try:
            result = await self.agent.analyze(self._request(difference))
            validate_action(difference, result.output)
            return await self.analyses.save_success(difference, result.output, result.provenance)
        except (ValidationError, AnalysisPolicyError, ModelProviderError) as error:
            await self.analyses.record_attempt(difference, attempt, error)
    return await self.analyses.save_manual_review(difference, attempt_count=2)
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/integration/ai/test_analysis_service.py -q`

Expected: deterministic, Agent fallback, retry, invalid policy, cached history, and manual-review tests PASS.

```bash
git add backend/app/ai/deterministic_analysis.py backend/app/ai/analysis_policy.py backend/app/ai/analysis_service.py backend/tests/integration/ai/test_analysis_service.py
git commit -m "feat: require validated difference analysis"
```

### Task 7: Expose analysis progress and execution eligibility

**Files:**
- Create: `backend/app/api/routes/analyses.py`
- Create: `backend/app/governance/eligibility.py`
- Modify: `backend/app/api/routes/differences.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/api/test_analyses.py`

**Interfaces:**
- Consumes: task/difference IDs and authenticated backend context.
- Produces: `POST /api/reconciliation-tasks/{id}/analyses`, `GET /api/differences/{id}/analysis`, analysis fields in difference pages, and `ExecutionEligibility.require_analyzed`.

- [ ] **Step 1: Write gating and history tests**

```python
def test_unanalyzed_difference_is_non_executable(client, pending_difference) -> None:
    body = client.get(f"/api/differences/{pending_difference.id}").json()
    assert body["analysis_status"] == "pending"
    assert body["execution_eligible"] is False

async def test_stale_analysis_version_is_rejected(eligibility, changed_difference) -> None:
    with pytest.raises(ExecutionIneligible, match="current difference version"):
        await eligibility.require_analyzed(changed_difference.id, changed_difference.version)
```

- [ ] **Step 2: Run API/gate tests**

Run: `cd backend && uv run pytest tests/integration/api/test_analyses.py -q`

Expected: FAIL because routes and eligibility service are absent.

- [ ] **Step 3: Implement backend-owned gate**

```python
class ExecutionEligibility:
    async def require_analyzed(self, difference_id: UUID, version: int) -> AnalysisResult:
        result = await self.analyses.get_for_difference(difference_id, version)
        if not result or result.status != "succeeded":
            raise ExecutionIneligible("valid analysis for current difference version is required")
        return result
```

- [ ] **Step 4: Add synchronous analysis trigger and query endpoints**

```python
@router.post("/reconciliation-tasks/{task_id}/analyses", status_code=202)
async def analyze_task(task_id: UUID, service=Depends(get_analysis_service)) -> AnalysisJobResponse:
    return await service.analyze_pending_for_task(task_id)

@router.get("/differences/{difference_id}/analysis", response_model=AnalysisResult)
async def get_analysis(difference_id: UUID, repo=Depends(get_analysis_repo)) -> AnalysisResult:
    return await repo.get_latest_or_404(difference_id)
```

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/unit/ai tests/integration/ai tests/integration/api/test_analyses.py -q`

Expected: progress, pending/failed/manual review, immutable history, stale version, and eligibility tests PASS.

```bash
git add backend/app/api/routes/analyses.py backend/app/api/routes/differences.py backend/app/governance/eligibility.py backend/app/main.py backend/tests/integration/api/test_analyses.py
git commit -m "feat: expose mandatory analysis gate"
```

### Task 8: Add adversarial and authorization acceptance coverage

**Files:**
- Create: `backend/tests/fixtures/model_outputs.py`
- Create: `backend/tests/integration/ai/test_analysis_security.py`
- Create: `backend/tests/integration/ai/test_analysis_provenance.py`

**Interfaces:**
- Consumes: malicious tool requests, prompt injection in CSV fields, invalid structured outputs, provider failures.
- Produces: regression proof that the Agent cannot broaden tools or bypass backend policy.

- [ ] **Step 1: Add adversarial parameterized cases**

```python
@pytest.mark.parametrize("output", [
    {}, {"cause": "x"}, {"cause": "x", "evidence_summary": "y", "recommended_action": "delete", "risk": "low", "confidence": .9},
    {"cause": "x", "evidence_summary": "y", "recommended_action": "update", "risk": "unknown", "confidence": .9},
])
async def test_invalid_outputs_never_become_executable(service, output, model_stub, difference) -> None:
    model_stub.always(output)
    result = await service.analyze(difference.id)
    assert result.status != "succeeded"
    assert await service.eligibility.is_eligible(difference.id, difference.version) is False
```

- [ ] **Step 2: Test prompt data cannot grant tools**

```python
async def test_csv_prompt_injection_cannot_add_mutation_tool(service, model_stub, injected_difference) -> None:
    model_stub.queue(tool_call("apply_target_update", {"id": "1"}))
    result = await service.analyze(injected_difference.id)
    assert result.status in {"failed", "manual_review"}
    assert target_connector_spy.apply_calls == 0
```

- [ ] **Step 3: Run the full AI suite and commit**

Run: `cd backend && uv run pytest tests/unit/ai tests/integration/ai tests/integration/api/test_analyses.py -q`

Expected: model-stub, invalid output, retry limit, prompt injection, MCP authorization, provenance, and manual-review tests PASS.

```bash
git add backend/tests/fixtures/model_outputs.py backend/tests/integration/ai
git commit -m "test: harden governed analysis boundaries"
```

## Module Acceptance

Run: `cd backend && uv run pytest tests/unit/ai tests/integration/ai tests/integration/api/test_analyses.py -q && uv run ruff check . && uv run mypy app`

Expected: every executable difference has a valid current-version analysis; clear cases avoid model calls; ambiguous cases use only authorized read tools; invalid or exhausted outputs remain non-executable; historical output and full AI provenance are queryable.

# Agent model contract reliability implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent valid DeepSeek JSON responses from exhausting Agent retries because their field shape drifts from the required contract, while preserving the four-attempt safety limit and accurately reporting the failed stage.

**Architecture:** Keep `complete_json_once` as one transport attempt and keep validation in the Supervisor/sub-agent boundary. For `json_object` providers, send the exact response schema and a concrete valid example in the prompt. On validation failure, make the next bounded attempt a corrective request containing only the previous JSON and safe validation feedback. Persist safe Supervisor failure categories in the terminal blocked event and derive the displayed business stage from the run phase when the graph is blocked.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy, pytest, React/TypeScript API contracts.

## Global constraints

- Retain one initial model call plus at most three retries.
- Never persist raw model output, prompts, credentials, or student phone values in failure events.
- Keep `json_schema` provider behavior unchanged.
- Continue accepting both `{ "result": {...} }` and flat JSON-object responses.
- Do not coerce invalid model fields into valid business decisions.

---

### Task 1: Make JSON-object contracts explicit

**Files:**
- Modify: `backend/app/ai/providers/base.py`
- Modify: `backend/app/ai/providers/llm.py`
- Modify: `backend/app/ai/agent_prompting.py`
- Modify: `backend/app/ai/graph_supervisor.py`
- Modify: `backend/app/ai/graph_subagents.py`
- Test: `backend/tests/unit/ai/test_llm_provider.py`
- Test: `backend/tests/unit/ai/test_graph_supervisor.py`
- Test: `backend/tests/integration/agent_graph/test_real_subagents.py`

**Interfaces:**
- Produces: `LLMRequest.response_example: dict[str, Any] | None`
- Produces: `build_json_repair_request(request, output, error) -> LLMRequest`
- Consumes: existing Pydantic response schemas and bounded retry loops.

- [x] Add failing provider tests proving `json_object` requests contain the exact schema and supplied JSON example.
- [x] Add failing Supervisor and sub-agent tests proving a malformed first response receives validation feedback and a corrected second response succeeds.
- [x] Run the focused tests and confirm they fail for missing contract/repair behavior.
- [x] Add the optional response example, provider prompt injection, and shared corrective-request builder.
- [x] Update Supervisor and sub-agent retry loops to use corrective requests after validation failures.
- [x] Run the focused tests and confirm they pass.

### Task 2: Preserve safe failure diagnostics

**Files:**
- Modify: `backend/app/ai/graph_supervisor.py`
- Modify: `backend/app/agent_graph/worker.py`
- Test: `backend/tests/unit/ai/test_graph_supervisor.py`
- Test: `backend/tests/integration/agent_graph/test_worker.py`

**Interfaces:**
- Produces: `GraphSupervisorFailure.failure_categories: tuple[str, ...]`
- Consumes: existing `run.blocked_model_error` event and graph transition guard payload.

- [x] Add failing tests proving four invalid decisions retain four safe failure categories without raw output.
- [x] Add failing worker test proving the blocked event identifies the original node and safe attempt categories.
- [x] Run the focused tests and confirm the expected failures.
- [x] Carry safe categories on `GraphSupervisorFailure` and persist them when blocking the run.
- [x] Run the focused tests and confirm they pass.

### Task 3: Display the real failed business stage

**Files:**
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/integration/api/test_agent_api.py`

**Interfaces:**
- Produces: `_graph_business_stage(node: str, run_phase: str | None) -> str`
- Consumes: the persisted run phase when `current_node == "blocked_model_error"`.

- [x] Add a failing API test proving a blocked source-inspection run reports `data_ingestion`, not `agent_analysis`.
- [x] Run the focused test and confirm the current default mapping fails.
- [x] Map blocked runs from their persisted phase while leaving ordinary graph-node mappings unchanged.
- [x] Run the focused test and confirm it passes.

### Task 4: Verify the complete fix

**Files:**
- No production files added.

- [x] Run all backend tests.
- [x] Run Ruff and mypy.
- [x] Run frontend tests, lint, typecheck, and build because the graph API contract drives the UI.
- [x] Run one configured DeepSeek diagnostic using the real Supervisor context and confirm the first response validates.
- [x] Review `git diff` for secrets, unrelated edits, and accidental raw model data.
- [x] Commit the verified implementation with Conventional Commit messages.

# Agent Conversation and Terminal Report Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct misleading Agent chat states, improve invalid model-response recovery, and make terminated or abnormal reports contain accurate actionable content.

**Architecture:** Keep message content separate from delivery/reply state in the React page, and render transport initialization failures outside the conversation history. Preserve the existing durable-message boundary in FastAPI while excluding UI error records from model history and repairing invalid JSON responses with validation feedback. Extend immutable report JSON facts with deterministic terminal context and render report sections according to terminal state.

**Tech Stack:** React 19, TypeScript, TanStack Query, Ant Design, Vitest, FastAPI, Pydantic 2, SQLAlchemy async, pytest.

## Global Constraints

- Preserve remote-link redaction: rejected URLs with query secrets must never be echoed by the client.
- Never claim an accepted durable user message was rejected merely because reply generation failed.
- Terminal reports must remain deterministic and must not depend on an LLM to let a task reach terminal state.
- Report facts remain server-owned and immutable; model output may narrate but may not alter facts.
- Use synthetic test data only.
- Do not add a database migration unless a persisted schema change is strictly required.

---

### Task 1: Conversation message and connection presentation

**Files:**
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

**Interfaces:**
- Consumes: `ApiError.code`, `AgentConversationApi.currentConversation()`, `createConversation()`, and `sendMessage()`.
- Produces: immediate plain-text user echo for ordinary messages, redaction-safe rejection behavior for link messages, and a page-level reconnect alert.

- [x] **Step 1: Write failing tests for immediate question echo and reply failure semantics**

Add a deferred `sendMessage` test that submits `你是谁` and asserts the user article contains `你是谁` before the promise settles and never contains `消息已提交，正在安全处理。`. Add a `conversation_model_error` test using `new ApiError(..., 502, "conversation_model_error")` and assert the original question remains visible while the assistant error is shown.

- [x] **Step 2: Run the focused tests and verify RED**

Run: `npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx`

Expected: failure because the user bubble contains the placeholder or is rewritten to `消息未被接受。`.

- [x] **Step 3: Implement separate content and error semantics**

In `sendMessage`, immediately echo ordinary message text. Keep the existing safe placeholder only when the raw input contains an HTTP(S) URL. In `catch`, preserve the submitted text for `conversation_model_error`; continue replacing rejected URL input for validation and transport failures where server acceptance is unknown.

- [x] **Step 4: Write a failing initialization-error test**

Reject `currentConversation()` with an `ApiError`, assert an Ant Design alert with a retry button is visible, and assert no assistant article contains `对话服务暂时不可用，请稍后重试。`.

- [x] **Step 5: Run the initialization test and verify RED**

Run: `npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx`

Expected: failure because initialization currently appends a fake assistant message.

- [x] **Step 6: Implement page-level connection recovery**

Extract the hydration request into a reusable callback, store `connectionError` independently from chat messages, render an `Alert` with a `重新连接` action, clear the alert on success, and keep the composer disabled while reconnecting.

- [x] **Step 7: Run the focused frontend test file and verify GREEN**

Run: `npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx`

Expected: all tests pass.

### Task 2: Conversation model recovery and clean history

**Files:**
- Modify: `backend/app/ai/conversation_agent.py`
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/unit/ai/test_conversation_agent.py`
- Test: `backend/tests/integration/api/test_agent_api.py`

**Interfaces:**
- Consumes: `build_json_repair_request(request, output, error)` and persisted `ConversationHistoryMessage.kind`.
- Produces: schema-aware retry requests and model context without assistant error placeholders.

- [x] **Step 1: Write a failing unit test for validation-feedback retry**

Make the first provider response omit `message_zh`, then return a valid response. Assert the second captured request includes an additional repair instruction and validation errors rather than being byte-for-byte equal to the first request.

- [x] **Step 2: Run the unit test and verify RED**

Run: `.venv/bin/pytest tests/unit/ai/test_conversation_agent.py::test_supervisor_retries_invalid_model_output_with_validation_feedback -q`

Expected: failure because `ConversationSupervisorAgent` currently resends the unchanged request.

- [x] **Step 3: Implement schema-aware repair**

Track the current `LLMRequest`. On `ConversationModelResponseError`, call `build_json_repair_request(current_request, response.output, error)` for the next attempt. On `ModelProviderError`, keep the current request unchanged and retry within the existing limit.

- [x] **Step 4: Write a failing integration test for history filtering**

Persist a user message, an assistant `kind="error"` message, and a new user message. Capture the next model request and assert the error assistant record is absent while durable user messages and normal assistant replies remain ordered.

- [x] **Step 5: Run the integration test and verify RED**

Run: `.venv/bin/pytest tests/integration/api/test_agent_api.py::test_conversation_model_history_excludes_assistant_error_messages -q`

Expected: failure because all persisted messages currently enter `ConversationAgentContext.history`.

- [x] **Step 6: Filter UI error records at the model boundary**

When building `history` in `send_agent_message`, exclude records where `role == "assistant" and kind == "error"`. Keep them in storage and in `GET /conversations/current` so the operator retains an audit-visible error state.

- [x] **Step 7: Run conversation unit and API tests and verify GREEN**

Run: `.venv/bin/pytest tests/unit/ai/test_conversation_agent.py tests/integration/api/test_agent_api.py -q`

Expected: all tests pass.

### Task 3: Deterministic terminal and abnormal report content

**Files:**
- Modify: `backend/app/agent_graph/production_executor.py`
- Test: `backend/tests/integration/agent_graph/test_production_runtime.py`
- Test: `backend/tests/integration/agent_graph/test_reporting.py`

**Interfaces:**
- Consumes: frozen facts from `build_agent_report_facts()` and `GraphWorkContext`.
- Produces: `facts.termination_context` and deterministic fallback `narrative.input_exception_analyses`.

- [x] **Step 1: Write a failing termination-report test**

Extend `test_termination_report_uses_verified_facts_without_calling_model` to assert `report.facts["termination_context"]` contains `reason_code`, `current_node`, `phase_zh`, `recorded_finding_count`, `succeeded_mutation_count`, `verified_mutation_count`, and `data_modified`, and that the narrative has a concrete `summary_zh`.

- [x] **Step 2: Run the termination test and verify RED**

Run: `.venv/bin/pytest tests/integration/agent_graph/test_production_runtime.py::test_termination_report_uses_verified_facts_without_calling_model -q`

Expected: failure because `termination_context` does not exist.

- [x] **Step 3: Implement deterministic termination context**

Before report generation, derive counts only from frozen `findings` and `mutations`. Write a stable context object with operator-requested reason, graph node, localized phase, count summaries, and verified-data-change status. Build the termination summary from those facts without calling the model.

- [x] **Step 4: Write a failing abnormal fallback test**

Force `GraphSubAgentFailure` for `abnormal_input_report`, provide `input_diagnostics.reason_counts`, and assert fallback `input_exception_analyses` has one deterministic entry per nonzero reason code.

- [x] **Step 5: Run the abnormal fallback test and verify RED**

Run: `.venv/bin/pytest tests/integration/agent_graph/test_production_runtime.py -k 'abnormal and fallback' -q`

Expected: failure because fallback narrative currently contains only title and summary.

- [x] **Step 6: Implement deterministic abnormal analyses**

Map known reason codes to safe Chinese title, impact, and suggestion; use a generic safe template for unknown codes. Sort codes for stable output and include counts from server facts.

- [x] **Step 7: Run report backend tests and verify GREEN**

Run: `.venv/bin/pytest tests/integration/agent_graph/test_production_runtime.py tests/integration/agent_graph/test_reporting.py -q`

Expected: all tests pass.

### Task 4: Terminal-state-aware report page

**Files:**
- Modify: `frontend/src/features/reports/AgentReportPage.tsx`
- Test: `frontend/src/features/reports/AgentReportPage.test.tsx`

**Interfaces:**
- Consumes: `AgentReport.terminal_state`, `facts.termination_context`, `narrative.input_exception_analyses`.
- Produces: truthful report sections for `terminated`, `abnormal_input`, `failed`, and `completed`.

- [x] **Step 1: Write failing terminated and abnormal report tests**

For a terminated report with empty findings/mutations, assert the page shows termination reason, stopped phase, completed counts, and “尚未进入问题分析” instead of “没有需要治理的问题”. For abnormal input, assert exception analysis is the primary content and the empty governance section does not claim no problems.

- [x] **Step 2: Run report page tests and verify RED**

Run: `npm test -- --run src/features/reports/AgentReportPage.test.tsx`

Expected: failure because the page always renders completed-task sections and generic empty states.

- [x] **Step 3: Implement terminal-state branches**

Add a terminated summary section backed by `termination_context`. Use state-specific empty descriptions: `任务在完成问题分析前已终止`, `输入异常阻止了治理分析`, and `任务失败前未形成可执行治理问题`. Keep the existing completed and rollback presentation unchanged.

- [x] **Step 4: Run report page tests and verify GREEN**

Run: `npm test -- --run src/features/reports/AgentReportPage.test.tsx`

Expected: all tests pass.

### Task 5: Integrated verification

**Files:**
- Verify all files changed by Tasks 1-4.

**Interfaces:**
- Consumes: completed frontend and backend fixes.
- Produces: evidence that the requested behaviors and repository quality gates pass.

- [x] **Step 1: Run focused frontend regression tests**

Run: `npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx src/features/reports/AgentReportPage.test.tsx`

- [x] **Step 2: Run frontend static checks**

Run: `npm run lint && npm run typecheck && npm run build`

- [x] **Step 3: Run focused backend regression tests**

Run: `.venv/bin/pytest tests/unit/ai/test_conversation_agent.py tests/integration/api/test_agent_api.py tests/integration/agent_graph/test_production_runtime.py tests/integration/agent_graph/test_reporting.py -q`

- [x] **Step 4: Run backend static checks**

Run: `.venv/bin/ruff check . && .venv/bin/mypy app`

- [x] **Step 5: Inspect the final diff**

Run: `git diff --check && git status --short && git diff --stat`

Expected: no whitespace errors; only scoped source, test, and planning files are changed.

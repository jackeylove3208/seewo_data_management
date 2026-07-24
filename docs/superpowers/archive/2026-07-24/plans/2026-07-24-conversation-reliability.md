# Agent Conversation Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agent conversations durable across navigation, accept the configured model provider's valid structured response, and terminate controlled-graph tasks through the required confirmation gate.

**Architecture:** Persist public user/assistant messages as tenant-scoped conversation facts and expose one resumable current-conversation view containing messages and any active task. Keep private intent in `AgentConversationRecord.context`; never persist prompts, credentials, raw provider payloads, or hidden reasoning. Normalize only the two explicitly supported model envelopes, then validate the strict decision schema. Reuse the existing controlled-graph termination preview and gate-decision APIs in the conversation UI.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- `tenant_id` comes only from trusted `OperatorContext`.
- Persist only public message text and role; do not persist system prompts or raw model responses.
- One current active conversation is resumable per demo tenant/operator.
- `agent-graph-v1` termination always requires a persisted confirmation gate.
- Legacy `new-agent-v1` termination continues to use the direct endpoint.
- New behavior must be introduced with a failing automated test first.

---

### Task 1: Accept and safely validate real conversation model output

**Files:**
- Modify: `backend/app/ai/conversation_agent.py`
- Modify: `backend/app/ai/skills/converse-school-data-sync/SKILL.md`
- Modify: `backend/app/api/routes/agent.py`
- Test: `backend/tests/unit/ai/test_conversation_agent.py`
- Test: `backend/tests/integration/api/test_agent_api.py`

**Interfaces:**
- Consumes: `LLMResponse.output: dict[str, object]`.
- Produces: a strict `ConversationAgentDecision` from either `{"result": {...}}` or a flat decision object, with the legacy field alias `type` normalized to `kind`.

- [x] Add a unit test whose provider returns `{"type": "clarification", "message_zh": "..."}` and verify the current implementation fails validation.
- [x] Add an API test proving invalid model output becomes a sanitized recoverable response rather than an unhandled 500.
- [x] Run the focused tests and confirm the expected failures.
- [x] Implement bounded envelope normalization, strict Pydantic validation, and sanitized provider/validation error mapping.
- [x] Clarify in the Skill that greetings and identity/capability questions receive a short truthful answer before redirecting to synchronization.
- [x] Run the focused tests and confirm they pass.

### Task 2: Persist and restore the active conversation

**Files:**
- Create: `backend/alembic/versions/0028_agent_conversation_messages.py`
- Modify: `backend/app/models/agent_runtime.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/agent_runtime/repository.py`
- Modify: `backend/app/schemas/agent_api.py`
- Modify: `backend/app/api/routes/agent.py`
- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Test: `backend/tests/integration/api/test_agent_api.py`
- Test: `backend/tests/integration/test_migrations.py`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

**Interfaces:**
- Produces: `GET /api/agent/conversations/current`, returning the latest active conversation, ordered public messages, and its latest active task when present.
- Produces: durable message records with `conversation_id`, `tenant_id`, monotonic `sequence`, `role`, `kind`, and `text`.
- Frontend consumes the current view before creating a new conversation.

- [x] Add backend tests proving user and assistant messages survive a second API read and cross-tenant reads are hidden.
- [x] Add a frontend test that unmounts/remounts the page and expects the backend conversation view to restore prior messages and task state.
- [x] Run both focused suites and verify they fail for missing persistence APIs.
- [x] Add the migration, model, repository methods, schemas, and current-conversation endpoint.
- [x] Persist the user message before model invocation and persist either the validated assistant response or sanitized recoverable error.
- [x] Hydrate the frontend from the current-conversation endpoint and create a conversation only when no resumable conversation exists.
- [x] Run focused backend, migration, and frontend tests and confirm they pass.

### Task 3: Use graph termination confirmation from the conversation UI

**Files:**
- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

**Interfaces:**
- Consumes: `previewTermination(taskId)` and `decideGraphGate(taskId, gateId, decision, reason)`.
- Preserves: direct `terminate(taskId)` for `new-agent-v1`.

- [x] Add a test that starts an `agent-graph-v1` task, clicks terminate, confirms the modal, and expects preview plus gate approval rather than direct termination.
- [x] Add a legacy-task assertion that direct termination remains available.
- [x] Run the focused test and verify the graph assertion fails.
- [x] Reuse the existing termination modal semantics from `AgentTaskDetailPage`.
- [x] Surface the real API error message instead of converting every failure into “后端不可用”.
- [x] Run the focused test and confirm it passes.

### Task 4: Full verification

**Files:**
- Modify: this plan only to check completed steps.

- [x] Run `PYTHONPATH=. .venv/bin/pytest --import-mode=importlib`.
- [x] Run the clean PostgreSQL migration smoke test through revision `0028`.
- [x] Run `.venv/bin/ruff check .` and `MYPYPATH=. .venv/bin/mypy app`.
- [x] Run `npm test -- --run`, `npm run lint`, `npm run typecheck`, `npm run build`, and `npm run test:e2e`.
- [x] Run `python3 dev.py --dry-run` and verify API, worker, and Vite remain in the launch plan.
- [x] Run `git diff --check` and inspect staged files before committing.

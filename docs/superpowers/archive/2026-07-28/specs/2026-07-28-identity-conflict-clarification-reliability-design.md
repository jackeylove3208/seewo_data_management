# Identity conflict clarification reliability design

## Context

CSV synchronization can pause on an identity conflict and ask the operator to choose a frozen
authoritative candidate or classify the target row as extra. The current task-detail and
conversation experiences use separate local state around the same backend gate.

The observed production-like run exposed three related reliability failures:

1. The task-detail clarification input remains editable while the model-backed request is in
   flight. Navigating away discards the local loading state, while the server still reports the
   clarification as `pending`, so the input can reopen and accept a duplicate submission.
2. An explicit candidate choice can still be routed through the model. In the observed run, all
   four model attempts read the frozen conflict but failed to submit the bounded structured
   interpretation, so the server incorrectly asked the operator to restate an already explicit
   choice.
3. The run later reached risk aggregation and failed with `KeyError: 'class_name'`. A valid
   cross-category correspondence compared a student authority row with a teacher target row;
   the change materializer indexed a target-only field that is absent for non-student records.

The baseline Playwright suite also has three failures: two ambiguous task-button locators and one
desktop-collapse test that does not create the `demo-001` fixture it asserts against.

## Goals

- Let operators make an explicit, bounded identity choice without model interpretation.
- Make a submitted choice immediately read-only and recover the same state after navigation.
- Support the full clarification flow from both the task-detail page and the new-conversation
  page.
- Preserve the previous read-only submission when the operator opens a new clarification form.
- Keep the existing second-confirmation safety boundary.
- Prevent missing cross-category fields from failing risk aggregation.
- Restore a reliable Playwright baseline and cover the new flow end to end.

## Non-goals

- Do not change graph versions, graph nodes, successor nodes, action ordering, or worker routing.
- Do not change identity matching, candidate generation, risk policy, approval ordering,
  execution planning, or target mutation semantics.
- Do not remove the legacy free-text clarification endpoint.
- Do not add an append-only submission-history table. The current requirement needs the active
  persisted submission and an unsaved replacement form, not an unbounded revision history.
- Do not retry or resume the already failed task automatically.

## State-graph invariant

The backend state graph execution order is immutable for this change. In particular, the existing
sync path remains:

```text
resolve_identity_conflicts
  -> analyze_actionable_batches
  -> aggregate_risk
  -> wait_high_risk_approvals
```

The structured selection endpoint may change an `AgentClarificationRecord` from `pending` to
`interpreted`. The existing confirmation endpoint may change it from `interpreted` to
`confirmed` and resume the waiting run. Neither endpoint may directly transition an
`AgentGraphRunRecord`, select a graph action, skip analysis, aggregate risk, or advance a graph
cursor. The existing `_resume_after_clarifications` behavior remains the only resume trigger.

## User experience

### Shared conflict card

Task detail and new conversation use one shared identity-conflict card and one shared controller.
The card displays the existing masked subject and candidate evidence, followed by:

- one radio option for every frozen candidate;
- an “按希沃多余处理” option only when `target_extra` is allowed;
- an optional clarification note of at most 500 characters; and
- a primary “提交选择” action.

Candidate labels are presentation-only (`候选 A`, `候选 B`, and so on). The submitted value is the
opaque candidate ID from the frozen server response.

### Submission

Clicking “提交选择” immediately replaces the editable controls with a read-only summary showing:

- the selected candidate or target-extra outcome;
- the operator’s optional note; and
- “正在保存” until the request completes.

The submit button disappears immediately. The shared controller optimistically updates the
query cache used by both routes, so ordinary route navigation does not reopen the editor while
the request is completing.

After the server accepts the selection, the summary changes to “等待确认” and shows:

- “重新选择”, which opens a new blank form without editing the current summary; and
- “确认选择并继续”, which calls the existing second-confirmation endpoint.

If the operator opens a replacement form, the previous submission stays visible and read-only.
Submitting the replacement atomically replaces the active persisted interpretation. Cancelling
the replacement leaves the previous interpretation unchanged.

### Revision feedback

The legacy free-text path may still produce revision feedback. When it does, the previous text
and server feedback remain read-only. A “补充说明” action opens a new blank form. It never makes
the old text editable.

### Confirmation

After confirmation, both entry points hide the actionable conflict card and show:

> 身份冲突选择已确认，Agent 正在继续处理。

Stale graph polling must not reopen a locally confirmed clarification. The next authoritative
graph response removes the approved gate normally.

## Frontend architecture

### Shared component

Create a shared component under `frontend/src/components/` for the complete clarification card.
It consumes:

- the current task ID;
- one actionable identity gate and its current conflict;
- the graph cursor;
- an `AgentConversationApi` implementation;
- an optional persisted or optimistic submission; and
- a callback that refreshes task, graph, and event data.

Both `AgentTaskDetailPage` and `ConversationCreatePage` render this component. The existing
`IdentityConflictEvidence` remains the evidence-only child.

### Shared controller

A shared hook/controller owns:

- selected decision and candidate ID;
- optional note;
- optimistic `saving` state;
- replacement-form state;
- request and stale-gate errors;
- submission and confirmation calls; and
- cache reconciliation after a response.

The pages no longer own separate clarification-message, interpretation, rewrite, and confirmation
state machines. Page-specific code remains responsible only for obtaining graph data and
refreshing its queries.

### Failure behavior

- A request failure restores the editable form with the operator’s selection and note intact.
- A stale gate or graph cursor clears the optimistic submission, refreshes the graph, and asks
  the operator to select from the refreshed candidates.
- A confirmation failure keeps the read-only submission and confirmation action visible.
- Terminal tasks never render actionable clarification controls.

## Backend API

### Structured selection endpoint

Add:

```http
POST /api/agent/tasks/{task_id}/clarifications/{clarification_id}/selection
```

Request:

```json
{
  "decision": "select_candidate",
  "selected_candidate_id": "uuid-or-null",
  "note": "optional operator note",
  "graph_cursor": 12,
  "idempotency_key": "client-generated-key"
}
```

Rules:

- `decision` is `select_candidate` or `treat_as_extra`.
- `selected_candidate_id` is required only for `select_candidate`.
- `note` is optional, trimmed, and limited to 500 characters.
- `graph_cursor` must match the current frozen graph cursor.
- `idempotency_key` is required and bounded.

Response uses the existing clarification interpretation shape and always requires second
confirmation:

```json
{
  "decision_id": "clarification-uuid",
  "status": "interpreted",
  "task_id": "task-uuid",
  "decision": "select_candidate",
  "selected_candidate_id": "candidate-uuid",
  "interpretation_zh": "你选择了第三方候选 A，确认后继续。",
  "requires_second_confirmation": true
}
```

### Validation

The endpoint rejects the request unless all of the following are true:

- task, run, clarification, and gate belong to the authenticated tenant;
- the task uses `agent-graph-v1`;
- the graph is currently at `resolve_identity_conflicts`;
- the identity gate is `pending` and contains the clarification ID;
- the graph cursor matches;
- frozen evidence remains complete;
- the decision is in the clarification’s allowed outcomes; and
- a selected candidate ID belongs to the frozen candidate set.

No operator-supplied label, entity data, tenant ID, task ID, or candidate evidence is trusted.

### Persistence and replay

The existing `AgentClarificationRecord` remains the source of truth:

- `status` becomes `interpreted`;
- `original_text` stores the optional note or a safe canonical structured-selection description;
- `interpretation` stores the bounded outcome, candidate ID, note, safe Chinese summary,
  idempotency key, and submission source `structured_selection`;
- `interpreted_by` and `updated_at` record the authenticated operator and current time; and
- `clarification_decision_ready` is appended through the existing runtime event repository.

A replay with the same idempotency key returns the persisted interpretation without appending a
second event. A new key while the record is still `interpreted` is treated as an explicit
replacement and overwrites the active interpretation. Once confirmed, replacements are rejected.

The legacy `POST /api/agent/tasks/{task_id}/clarification` endpoint and its model-backed behavior
remain available for compatibility.

### Graph response

Each `AgentGraphIdentityConflict` gains an optional `operator_submission`:

```json
{
  "decision": "select_candidate",
  "selected_candidate_id": "candidate-uuid",
  "note": "optional note",
  "interpretation_zh": "你选择了第三方候选 A，确认后继续。",
  "submitted_at": "2026-07-28T09:00:00Z",
  "source": "structured_selection"
}
```

The response is authorized by the existing task/tenant checks. It never includes unmasked phone
values, internal prompts, model output traces, or candidate data not already exposed by the
frozen conflict view.

## Risk-aggregation repair

`_changed_values` currently evaluates `target_values[key]` as the default argument to
`raw_values.get`. For a student authority row paired with a non-student target row,
`ordinary_field_differences` legitimately includes `class_name`, while `_record_values(target)`
does not contain that key.

The repair treats an absent old normalized field as `None`:

```python
raw_values.get(key, target_values.get(key))
```

The authority-side new value, changed-field set, risk grouping, approval creation, graph action,
and graph successor remain unchanged. A focused test covers student-authority/teacher-target and
teacher-authority/student-target correspondences.

## Playwright baseline repair

- Scope the task-opening locator to the history task region or use the task-row/link semantic
  target, so delete buttons cannot satisfy the same locator.
- Give the desktop-collapse test its own `demo-001` fixture and route setup before navigation.
- Add a graph identity-conflict E2E flow that completes structured selection and confirmation
  from the conversation page, then verifies the same persisted state from task detail.
- Add a task-detail navigation check that submits a selection, leaves the route, returns, and
  observes a read-only submission with no submit button.

## Test strategy

### Backend

- Repository tests for structured interpretation replay, replacement, and confirmed-state
  rejection.
- API tests for valid candidate selection, target-extra selection, cross-tenant access, stale
  cursor, stale gate, unknown candidate, invalid outcome, duplicate idempotency key, replacement,
  and confirmation.
- An assertion that the structured endpoint never invokes the model provider.
- Graph serialization tests for `operator_submission`.
- Risk-aggregation regression tests for absent cross-category `class_name`.
- Existing graph definition and transition tests must remain byte-for-byte equivalent in expected
  node/action ordering.

### Frontend

- Shared component tests for selection, optional note, immediate read-only state, replacement,
  failure restoration, stale refresh, and confirmation.
- Task-detail integration tests for leave-and-return persistence.
- Conversation integration tests for completing the whole flow without leaving chat.
- Compatibility tests for persisted legacy revision feedback.
- Updated Playwright tests described above.

### Quality gates

Run:

```bash
cd backend
PYTHONPATH="$PWD" .venv/bin/pytest --import-mode=importlib
.venv/bin/ruff check .
.venv/bin/mypy app
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
cd ../frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
npm run test:e2e
cd ..
openspec validate --all --strict --no-interactive
```

No model credentials or production data are required.

## Security and privacy

- Candidate choice is bound to authenticated tenant, task, run, graph cursor, gate, and frozen
  candidate IDs.
- The server ignores client-supplied candidate labels and evidence.
- Optional notes are never written to logs or model prompts by the structured path.
- Existing phone masking and task-level authorization remain unchanged.
- The endpoint cannot approve risk groups, mutate target data, release the school lock, or change
  graph order.

## Acceptance criteria

- Submitting a structured choice immediately removes the submit button and editor.
- The submitted choice and optional note remain visible and read-only.
- Navigating between task detail and new conversation preserves the same persisted submission.
- Both pages can select, replace, confirm, and complete the clarification flow.
- Explicit structured choices never produce model revision feedback.
- Confirmed clarifications resume through the existing graph path without changing node order.
- Cross-category risk aggregation no longer fails on a missing `class_name`.
- Backend, frontend, Playwright, migration, and OpenSpec quality gates pass.

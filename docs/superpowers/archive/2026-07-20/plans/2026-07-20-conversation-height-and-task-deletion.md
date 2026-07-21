# Conversation Height and Task Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the Agent conversation to the available viewport height and let operators permanently delete analyzed reconciliation tasks that have never produced a governance proposal.

**Architecture:** A tenant-scoped backend deletion service owns eligibility and foreign-key-safe cleanup, exposed through one DELETE endpoint. Frontend history surfaces share a small deletion controller that confirms, calls the API, and removes local history only after success; conversation sizing remains CSS-only.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, pytest, React 19, TypeScript, Ant Design, Vitest, Testing Library, Playwright.

## Global Constraints

- A task is deletable only when an `analysis` workflow stage has `status="succeeded"` and no `governance_proposals` row exists for the task.
- Missing and cross-tenant tasks return `404`; incomplete analysis and proposal-protected tasks return `409` with distinct Chinese messages.
- Demo tasks, bulk deletion, undo, retention policies, Agent synchronization, and governance execution are out of scope.
- Task-owned database rows are deleted in a single transaction; stored paths come only from persisted records.
- The frontend removes local history only after backend success and never optimistically hides a task.
- Preserve the user's unrelated backend migration, security tests, and `add-ai-find` work.

---

### Task 1: Add the guarded backend deletion service

**Files:**
- Create: `backend/app/tasks/__init__.py`
- Create: `backend/app/tasks/deletion_service.py`
- Create: `backend/tests/integration/tasks/test_task_deletion.py`

**Interfaces:**
- Consumes: `AsyncSession`, `ReconciliationTask`, `WorkflowStageRun`, `GovernanceProposalRecord`, and current task-owned SQLAlchemy models.
- Produces: `TaskDeletionService.delete(task_id: UUID, tenant_id: str) -> None`, `TaskDeletionNotFound`, and `TaskDeletionBlocked(message: str)`.

- [ ] **Step 1: Write service tests for eligibility before mutation**

Create async tests that seed two minimal tasks and assert:

```python
with pytest.raises(TaskDeletionBlocked, match="尚未完成 AI 分析"):
    await TaskDeletionService(session).delete(unanalysed.id, "school-1")

session.add(WorkflowStageRun(
    id=uuid4(), task_id=analysed.id, stage="analysis", attempt=1,
    status="succeeded", processed=1, total=1, succeeded=1,
    manual_review=0, failed=0, retryable=False,
    started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
))
await session.flush()
await TaskDeletionService(session).delete(analysed.id, "school-1")
assert await session.get(ReconciliationTask, analysed.id) is None
assert await session.get(ReconciliationTask, other.id) is not None
```

Add a proposal-protection test using a real difference, analysis record, and `GovernanceProposalRecord`; expect `TaskDeletionBlocked("该任务已有治理方案，不能删除")`. Add missing and cross-tenant tests expecting `TaskDeletionNotFound`.

- [ ] **Step 2: Run the focused backend test and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/tasks/test_task_deletion.py -q
```

Expected: FAIL because `app.tasks.deletion_service` does not exist.

- [ ] **Step 3: Implement eligibility checks and ordered Core deletes**

Implement stable exceptions and checks:

```python
class TaskDeletionNotFound(LookupError):
    pass

class TaskDeletionBlocked(ValueError):
    pass

analysis_complete = await session.scalar(select(exists().where(
    WorkflowStageRun.task_id == task_id,
    WorkflowStageRun.stage == "analysis",
    WorkflowStageRun.status == "succeeded",
)))
proposal_exists = await session.scalar(select(exists().where(
    GovernanceProposalRecord.task_id == task_id,
)))
```

Collect `SourceFile.storage_path` and `Snapshot.quarantine_path`, then execute SQLAlchemy Core `delete()` statements in this order so mapper immutability events are not weakened:

1. `AnalysisWorkItemRecord`
2. `ProposalBatchRecord`
3. `AnalysisJobRecord`
4. `AnalysisRecord` selected through task differences
5. `DifferenceRecord`
6. `TargetEntityEmbedding`
7. `EntityMapping`
8. `CanonicalEntityRecord`, `RawSnapshotRow`, and `IngestionIssueRecord`
9. `Snapshot`
10. `WorkflowStageRun`
11. `SourceFile`
12. `ReconciliationTask`

Use task-scoped subqueries captured before parent deletion. Commit before unlinking persisted paths, then call `Path(path).unlink(missing_ok=True)` and log cleanup failures without changing the successful deletion result.

- [ ] **Step 4: Add cleanup-isolation and idempotent-file tests**

Seed source and quarantine files under `tmp_path`, delete the eligible task, and assert its files and rows are gone while another task's rows and files remain. Call `unlink(missing_ok=True)` behavior with one already-missing file. Assert a second service deletion raises `TaskDeletionNotFound`.

- [ ] **Step 5: Run focused backend tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/tasks/test_task_deletion.py -q
.venv/bin/ruff check app/tasks tests/integration/tasks
.venv/bin/mypy app/tasks
```

Expected: all commands exit `0`.

- [ ] **Step 6: Commit the deletion service**

```bash
git add backend/app/tasks backend/tests/integration/tasks/test_task_deletion.py
git commit -m "feat: add guarded task deletion service"
```

---

### Task 2: Expose tenant-safe task deletion through FastAPI

**Files:**
- Modify: `backend/app/api/routes/reconciliation_tasks.py`
- Modify: `backend/tests/integration/api/test_ingestion_api.py`

**Interfaces:**
- Consumes: `TaskDeletionService.delete(task_id, operator.tenant_id)` and its stable exceptions.
- Produces: `DELETE /api/reconciliation-tasks/{task_id}` returning `204`, `404`, or `409`.

- [ ] **Step 1: Write API tests for response contracts**

Add tests using the existing TestClient and database fixture helpers:

```python
response = client.delete(f"/api/reconciliation-tasks/{task_id}")
assert response.status_code == 204
assert response.content == b""
assert client.get(f"/api/reconciliation-tasks/{task_id}").status_code == 404
```

Add cases for incomplete analysis (`409`, exact detail), protected proposal (`409`, exact detail), repeated deletion (`404`), and cross-tenant deletion (`404`).

- [ ] **Step 2: Run the API tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_ingestion_api.py -k "delete" -q
```

Expected: FAIL with `405 Method Not Allowed`.

- [ ] **Step 3: Add the DELETE route**

Implement:

```python
@router.delete("/reconciliation-tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reconciliation_task(
    task_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> None:
    try:
        await TaskDeletionService(session).delete(task_id, operator.tenant_id)
    except TaskDeletionNotFound as error:
        raise HTTPException(404, detail=str(error)) from error
    except TaskDeletionBlocked as error:
        raise HTTPException(409, detail=str(error)) from error
```

- [ ] **Step 4: Run API and backend regression tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_ingestion_api.py -q
.venv/bin/pytest tests/integration/tasks/test_task_deletion.py -q
.venv/bin/ruff check app tests/integration/api/test_ingestion_api.py tests/integration/tasks
.venv/bin/mypy app
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the API boundary**

```bash
git add backend/app/api/routes/reconciliation_tasks.py backend/tests/integration/api/test_ingestion_api.py
git commit -m "feat: expose eligible task deletion"
```

---

### Task 3: Add frontend deletion state and history controls

**Files:**
- Modify: `frontend/src/api/ingestion.ts`
- Modify: `frontend/src/data/taskHistory.ts`
- Create: `frontend/src/features/tasks/useTaskDeletion.tsx`
- Create: `frontend/src/features/tasks/useTaskDeletion.test.tsx`
- Modify: `frontend/src/app/WorkspaceSidebar.tsx`
- Modify: `frontend/src/app/WorkspaceSidebar.test.tsx`
- Modify: `frontend/src/features/tasks/TaskListPage.tsx`
- Modify: `frontend/src/features/tasks/TaskListPage.test.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**
- Produces: `ingestionApi.deleteTask(taskId: string): Promise<void>`, `removeStoredTask(taskId: string): void`, and `useTaskDeletion(): { requestDelete(task): void; confirmation: ReactNode }`.
- Consumes: `TaskHistoryItem`, `ApiError`, Ant Design `Modal`, `message`, and `TASK_HISTORY_UPDATED_EVENT`.

- [ ] **Step 1: Write failing storage and controller tests**

Assert `removeStoredTask("task-1")` preserves other tasks and dispatches one update event. Render a controller harness and assert:

```tsx
await user.click(screen.getByRole("button", { name: "删除真实任务" }));
expect(screen.getByRole("dialog", { name: "删除任务" })).toBeInTheDocument();
await user.click(screen.getByRole("button", { name: "取消" }));
expect(fetch).not.toHaveBeenCalled();
```

Add success, pending duplicate prevention, `409`, and network-failure tests. Success must remove local history only after the DELETE response resolves.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/features/tasks/useTaskDeletion.test.tsx src/app/WorkspaceSidebar.test.tsx src/features/tasks/TaskListPage.test.tsx
```

Expected: FAIL because deletion helpers and controls do not exist.

- [ ] **Step 3: Implement API, storage, and shared controller**

Add:

```ts
async function deleteTask(taskId: string) {
  await requestJson<void>(`/api/reconciliation-tasks/${taskId}`, { method: "DELETE" });
}

export function removeStoredTask(taskId: string) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(
    getStoredTasks().filter((task) => task.id !== taskId),
  ));
  window.dispatchEvent(new Event(TASK_HISTORY_UPDATED_EVENT));
}
```

Because `204` has no JSON body, update `requestJson` to return `undefined as T` when `response.status === 204`, with a focused client test.

The controller owns selected task, pending state, confirmation, and error feedback. It never accepts demo tasks and calls `removeStoredTask` only after `ingestionApi.deleteTask` resolves.

- [ ] **Step 4: Add accessible controls to sidebar and task list**

Render a sibling icon button using Lucide `Trash2`, `aria-label={`删除${task.title}`}`, and a tooltip/title. Do not nest a button inside `NavLink` or the task-row button. Demo tasks render navigation only. Stop delete clicks from navigating and close the mobile drawer only on actual navigation.

Update CSS with stable action tracks, 36px icon buttons, visible focus, mobile stacking, and collapsed-sidebar hiding. Preserve existing task-row and recent-history dimensions.

- [ ] **Step 5: Run focused frontend tests and verify GREEN**

Run:

```bash
cd frontend
npm test -- --run src/api/client.test.ts src/features/tasks/useTaskDeletion.test.tsx src/app/WorkspaceSidebar.test.tsx src/features/tasks/TaskListPage.test.tsx
npm run lint
npm run typecheck
```

Expected: all commands exit `0`.

- [ ] **Step 6: Commit frontend deletion**

```bash
git add frontend/src/api frontend/src/data/taskHistory.ts frontend/src/features/tasks frontend/src/app/WorkspaceSidebar.tsx frontend/src/app/WorkspaceSidebar.test.tsx frontend/src/styles/global.css
git commit -m "feat: delete eligible task history"
```

---

### Task 4: Expand the Agent conversation workspace

**Files:**
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/styles/global.test.ts`
- Modify: `frontend/tests/e2e/reconciliation-flow.spec.ts`

**Interfaces:**
- Consumes: existing `.conversation-create-page`, `.conversation-surface`, `.conversation-messages`, and `.conversation-composer` markup.
- Produces: a viewport-bounded conversation whose message list grows and scrolls while the composer remains visible.

- [ ] **Step 1: Add failing CSS contract and browser assertions**

Extend the CSS test to require viewport sizing and removal of the old `max-height: 410px`. Add Playwright assertions on desktop and mobile:

```ts
const surface = await page.locator(".conversation-surface").boundingBox();
const composer = await page.locator(".conversation-composer").boundingBox();
expect(surface!.height).toBeGreaterThan(testInfo.project.name === "desktop" ? 520 : 400);
expect(composer!.y + composer!.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1);
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/styles/global.test.ts
PLAYWRIGHT_PORT=5199 npm run test:e2e -- --grep "conversation workspace"
```

Expected: FAIL because the message area still has a `410px` maximum.

- [ ] **Step 3: Implement responsive vertical sizing**

Use grid/flex tracks and dynamic viewport constraints:

```css
.conversation-create-page {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: calc(100dvh - 56px);
}

.conversation-surface {
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  min-height: 520px;
  height: calc(100dvh - 170px);
  max-height: 820px;
}

.conversation-messages {
  min-height: 0;
  max-height: none;
}
```

Tune the mobile media query with a minimum around `400px` and `height: calc(100dvh - 130px)` so the composer remains visible without overlap. Keep message overflow internal.

- [ ] **Step 4: Run focused and visual tests**

Run:

```bash
cd frontend
npm test -- --run src/styles/global.test.ts
PLAYWRIGHT_PORT=5199 npm run test:e2e -- --grep "conversation workspace"
```

Expected: desktop and mobile assertions pass without overlap or horizontal overflow.

- [ ] **Step 5: Commit conversation sizing**

```bash
git add frontend/src/styles/global.css frontend/src/styles/global.test.ts frontend/tests/e2e/reconciliation-flow.spec.ts
git commit -m "feat: expand agent conversation workspace"
```

---

### Task 5: Verify the complete workflow and document the change

**Files:**
- Modify: `openspec/changes/v2-ui/design.md`
- Modify: `openspec/changes/v2-ui/specs/conversational-task-creation/spec.md`
- Modify: `openspec/changes/v2-ui/tasks.md`

**Interfaces:**
- Consumes: completed backend DELETE contract and frontend behavior.
- Produces: coherent V2 specification and complete verification evidence.

- [ ] **Step 1: Update V2 OpenSpec**

Document the full-height conversation, deletion availability in both history surfaces, successful-analysis requirement, permanent proposal protection, tenant safety, confirmation, and non-optimistic error behavior. Add completed task items and do not change Agent synchronization scope.

- [ ] **Step 2: Add end-to-end deletion coverage**

Route a successful DELETE in Playwright, seed a real local history task, confirm deletion from the full list, and assert both list and sidebar remove it. Add a `409` route and assert the task remains with the protected-task message. Verify delete controls do not exist for demo tasks.

- [ ] **Step 3: Run complete verification sequentially**

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
PLAYWRIGHT_PORT=5199 npm run test:e2e
cd ..
openspec validate v2-ui
openspec validate optimize-ai-analysis-workflow
```

Expected: every command exits `0`; project-specific Playwright skips remain expected.

- [ ] **Step 4: Inspect scope and commit documentation/E2E**

Confirm `git status --short` contains no user backend migration, security-test, or `add-ai-find` changes. Then run:

```bash
git add frontend/tests/e2e/reconciliation-flow.spec.ts openspec/changes/v2-ui
git commit -m "docs: specify analyzed task deletion"
```

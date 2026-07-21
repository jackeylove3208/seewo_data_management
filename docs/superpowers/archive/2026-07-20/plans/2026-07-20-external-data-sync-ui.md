# External Data Sync UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the combined conversational task-creation screen with a progressive manual CSV synchronization workflow while preserving the existing task creation and downstream reconciliation process.

**Architecture:** Keep `/tasks/new`, `TaskDraft`, `createTaskFromDraft`, and all backend APIs unchanged. Split the visible product entry into an unavailable future conversation command and an active external-data-sync route, then use local page state to reveal the existing paired-CSV form only after the user selects manual sync.

**Tech Stack:** React 19, TypeScript 5.8, React Router 7, Ant Design 5, lucide-react, Vitest, Testing Library, Playwright, CSS.

## Global Constraints

- This is a frontend-only change; do not modify backend code, migrations, API contracts, or workflow stages.
- `新建对话` is visible and disabled with `即将开放`; do not create an Agent route or placeholder page.
- `外部数据同步` replaces every user-facing `新建对账` command and continues to use `/tasks/new`.
- CSV controls remain absent until `手动同步` is selected.
- `系统自动同步` is visible, disabled, and labelled `暂未开放`.
- Do not render chat messages, a composer, assistant thinking states, Agent branding, or a visible `任务草案` panel on `/tasks/new`.
- Reuse the existing paired upload, idempotency key, task creation, history refresh, and success navigation behavior.
- After task creation, data ingestion, entity resolution, difference detection, and AI analysis remain unchanged.
- Preserve current desktop sidebar collapse behavior, mobile drawer behavior, direct accessible names, and no-overlap layout.
- Preserve unrelated dirty work in the main checkout and make all implementation commits in the feature worktree.

---

### Task 1: Separate workspace commands and rename task creation entry

**Files:**
- Modify: `frontend/src/app/WorkspaceSidebar.tsx:1-135`
- Modify: `frontend/src/app/WorkspaceSidebar.test.tsx:9-80`
- Modify: `frontend/src/features/tasks/TaskListPage.tsx:1-38`
- Modify: `frontend/src/features/tasks/TaskListPage.test.tsx:1-32`

**Interfaces:**
- Consumes: existing `/tasks/new` route and `onMobileClose(): void` callback.
- Produces: disabled `新建对话` button with accessible name `新建对话，即将开放`; active `外部数据同步` `NavLink` to `/tasks/new`.

- [ ] **Step 1: Write failing workspace and task-list tests**

Add this case to `WorkspaceSidebar.test.tsx`:

```tsx
it("separates the future conversation from external data sync", () => {
  render(
    <MemoryRouter initialEntries={["/tasks/new"]}>
      <WorkspaceSidebar mobileOpen={false} onMobileClose={() => undefined} />
    </MemoryRouter>,
  );

  expect(screen.getByRole("button", { name: "新建对话，即将开放" })).toBeDisabled();
  expect(screen.getByRole("link", { name: "外部数据同步" })).toHaveAttribute("href", "/tasks/new");
  expect(screen.getByRole("link", { name: "外部数据同步" })).toHaveAttribute("aria-current", "page");
  expect(screen.queryByRole("link", { name: "新建对账" })).not.toBeInTheDocument();
});
```

Add this case to `TaskListPage.test.tsx`:

```tsx
it("opens external data sync from the task list", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={["/tasks"]}>
      <Routes>
        <Route path="/tasks" element={<TaskListPage />} />
        <Route path="/tasks/new" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );

  await user.click(screen.getByRole("button", { name: "外部数据同步" }));
  expect(screen.getByText("/tasks/new")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/app/WorkspaceSidebar.test.tsx src/features/tasks/TaskListPage.test.tsx
```

Expected: FAIL because `新建对话，即将开放` and `外部数据同步` do not exist.

- [ ] **Step 3: Implement the separate workspace commands**

In `WorkspaceSidebar.tsx`, replace the single new-task link with this command group and use lucide `MessageSquarePlus` and `RefreshCw` icons:

```tsx
<div className="workspace-primary-actions" aria-label="主要操作">
  <button
    className="workspace-agent-entry"
    type="button"
    aria-label="新建对话，即将开放"
    title="新建对话，即将开放"
    disabled
  >
    <MessageSquarePlus size={18} />
    <span className="workspace-label workspace-command-copy">
      <strong>新建对话</strong>
      <small>即将开放</small>
    </span>
  </button>
  <NavLink className="workspace-new-task" to="/tasks/new" onClick={onMobileClose}>
    <RefreshCw size={18} />
    <span className="workspace-label">外部数据同步</span>
  </NavLink>
</div>
```

Change the task-list primary button text from `新建对账` to `外部数据同步`. Do not change routing.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command.

Expected: all selected test files PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add frontend/src/app/WorkspaceSidebar.tsx frontend/src/app/WorkspaceSidebar.test.tsx frontend/src/features/tasks/TaskListPage.tsx frontend/src/features/tasks/TaskListPage.test.tsx
git commit -m "feat: separate workspace sync entry"
```

---

### Task 2: Replace conversational creation with progressive manual sync

**Files:**
- Modify: `frontend/src/features/task-create/TaskCreatePage.tsx:1-230`
- Modify: `frontend/src/features/task-create/TaskCreatePage.test.tsx:1-121`
- Modify: `frontend/src/app/App.test.tsx:11-24`
- Keep unchanged: `frontend/src/features/task-create/taskCreationService.ts`
- Keep unchanged: `frontend/src/features/task-create/types.ts`
- Keep unchanged: `frontend/src/features/task-create/assistant.ts`

**Interfaces:**
- Consumes: `createInitialDraft(): TaskDraft`, `summarizeCsv(file): Promise<CsvSummary>`, `isDraftReady(draft): boolean`, and `createTaskFromDraft(draft, idempotencyKey)`.
- Produces: `/tasks/new` page with local `syncMethod: "manual" | null`, manual CSV form, and `开始同步` submission action.

- [ ] **Step 1: Replace conversation expectations with failing sync-method tests**

Replace the initial page test with:

```tsx
it("reveals CSV controls only after manual sync is selected", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <TaskCreatePage />
    </MemoryRouter>,
  );

  expect(screen.getByRole("heading", { name: "外部数据同步" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "系统自动同步，暂未开放" })).toBeDisabled();
  expect(screen.queryByLabelText("选择三方系统 CSV")).not.toBeInTheDocument();
  expect(screen.queryByText("任务草案")).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "对账要求" })).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "手动同步" }));

  expect(screen.getByLabelText("选择三方系统 CSV")).toBeInTheDocument();
  expect(screen.getByLabelText("选择希沃魔方 CSV")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始同步" })).toBeDisabled();
});
```

Update the first `App.test.tsx` case so it clicks the `外部数据同步` link and expects the `外部数据同步` heading after navigation.

Update the successful creation, submission failure, and duplicate-submit tests so each first clicks `手动同步`, uploads both CSV files, and operates the `开始同步` button. Keep their existing API mocks and stable-idempotency assertions.

Add this form-validation test:

```tsx
it("requires complete manual sync settings before submission", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><TaskCreatePage /></MemoryRouter>);
  await user.click(screen.getByRole("button", { name: "手动同步" }));

  const startButton = screen.getByRole("button", { name: "开始同步" });
  await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File([csv], "third-party.csv", { type: "text/csv" }));
  await user.upload(screen.getByLabelText("选择希沃魔方 CSV"), new File([csv], "mofa.csv", { type: "text/csv" }));
  await waitFor(() => expect(startButton).toBeEnabled());

  await user.clear(screen.getByRole("textbox", { name: "同步任务名称" }));
  expect(startButton).toBeDisabled();
});
```

- [ ] **Step 2: Run page tests and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/TaskCreatePage.test.tsx src/app/App.test.tsx
```

Expected: FAIL because the page still contains the conversation, renders CSV inputs immediately, and uses `创建对账`.

- [ ] **Step 3: Implement progressive manual sync**

Keep `AttachmentPicker`, but change its root class to `sync-attachment`. Remove all conversation imports and state: `ArrowUp`, `Bot`, `Sparkles`, `UserRound`, `FormEvent`, `deterministicTaskAssistant`, `ConversationMessage`, message IDs, messages, input, and `sendMessage`.

Use this page-state shape:

```tsx
type SubmissionState = "idle" | "submitting" | "failed" | "created";

const [syncMethod, setSyncMethod] = useState<"manual" | null>(null);
const [draft, setDraft] = useState<TaskDraft>(() => createInitialDraft());
const [submissionState, setSubmissionState] = useState<SubmissionState>("idle");
const [submitError, setSubmitError] = useState<string>();
```

`prepareFile` updates only the attachment and summary. `createTask` checks `isDraftReady(draft)` and `submissionState === "submitting"`, calls the unchanged `createTaskFromDraft`, and navigates to the created task.

Render this method selector before the conditional form:

```tsx
<section className="sync-methods" aria-labelledby="sync-method-title">
  <div className="section-title-row">
    <div>
      <h2 id="sync-method-title">选择同步方式</h2>
      <p>先选择数据进入方式，再配置本次同步范围。</p>
    </div>
  </div>
  <div className="sync-method-grid">
    <button
      className={syncMethod === "manual" ? "sync-method active" : "sync-method"}
      type="button"
      aria-pressed={syncMethod === "manual"}
      onClick={() => setSyncMethod("manual")}
    >
      <FileUp size={20} />
      <span><strong>手动同步</strong><small>上传三方系统与希沃魔方 CSV</small></span>
    </button>
    <button className="sync-method" type="button" aria-label="系统自动同步，暂未开放" disabled>
      <CloudCog size={20} />
      <span><strong>系统自动同步</strong><small>暂未开放</small></span>
    </button>
  </div>
</section>
```

When `syncMethod === "manual"`, render one `<section className="manual-sync-form" aria-label="手动同步配置">` containing the two attachment pickers, task name input with `aria-label="同步任务名称"`, scope input with `aria-label="核对范围"`, the existing full/partial segmented control, entity checkboxes, direct data summaries, error alert, and:

```tsx
<Button
  className="sync-start-button"
  type="primary"
  size="large"
  loading={submissionState === "submitting"}
  disabled={!ready || submissionState === "submitting"}
  onClick={() => void createTask()}
>
  开始同步
</Button>
```

Use heading copy `外部数据同步` and supporting copy `通过手动文件同步创建对账任务，后续处理流程保持不变。`.

- [ ] **Step 4: Run page and service tests and verify GREEN**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/TaskCreatePage.test.tsx src/features/task-create/taskCreationService.test.ts src/app/App.test.tsx
```

Expected: all three test files PASS and task-creation service behavior remains unchanged.

- [ ] **Step 5: Commit Task 2**

```bash
git add frontend/src/features/task-create/TaskCreatePage.tsx frontend/src/features/task-create/TaskCreatePage.test.tsx frontend/src/app/App.test.tsx
git commit -m "feat: build manual external data sync"
```

---

### Task 3: Complete responsive styling and end-to-end regression coverage

**Files:**
- Modify: `frontend/src/styles/global.css:153-176,1224-1626,1703-1714`
- Modify: `frontend/tests/e2e/reconciliation-flow.spec.ts:120-169`
- Test: `frontend/src/app/WorkspaceSidebar.test.tsx`
- Test: `frontend/src/features/task-create/TaskCreatePage.test.tsx`

**Interfaces:**
- Consumes: Task 1 classes `workspace-primary-actions`, `workspace-agent-entry`, `workspace-command-copy`; Task 2 classes `external-sync-page`, `sync-methods`, `sync-method-grid`, `sync-method`, `manual-sync-form`, `sync-attachments`, `sync-attachment`, `sync-settings-grid`, `sync-start-button`.
- Produces: stable desktop/mobile layout and an end-to-end manual CSV synchronization flow.

- [ ] **Step 1: Update the Playwright flow before changing styles**

Rename the existing test to `creates a task from manual external data sync` and replace its page interaction with:

```ts
await page.goto("/tasks/new");
await expect(page.getByRole("heading", { name: "外部数据同步" })).toBeVisible();
await expect(page.getByLabel("选择三方系统 CSV")).toHaveCount(0);
await page.getByRole("button", { name: "手动同步" }).click();
await page.getByLabel("核对范围").fill("七年级");
await page.getByRole("button", { name: "指定范围" }).click();
await page.getByLabel("部门").uncheck();
await page.getByLabel("班级").uncheck();
await page.getByLabel("选择三方系统 CSV").setInputFiles({ name: "third-party.csv", mimeType: "text/csv", buffer: csv });
await page.getByLabel("选择希沃魔方 CSV").setInputFiles({ name: "mofa.csv", mimeType: "text/csv", buffer: csv });
await expect(page.getByRole("button", { name: "开始同步" })).toBeEnabled();
await page.getByRole("button", { name: "开始同步" }).click();
await expect(page).toHaveURL(/\/tasks\/task-created$/);
```

Keep the existing API routes and sidebar-history assertion. Add desktop bounds checks for `.manual-sync-form`, and on mobile assert the method buttons and attachment selectors fit inside the viewport.

Add this explicit style assertion so the test is RED before the new CSS exists:

```ts
await expect(page.locator(".sync-method-grid")).toHaveCSS("display", "grid");
```

- [ ] **Step 2: Run the focused Playwright test and observe the current layout failure**

Run:

```bash
cd frontend
npx playwright test tests/e2e/reconciliation-flow.spec.ts --grep "manual external data sync" --project=desktop
```

Expected before the CSS implementation: the functional flow may pass, but the new class-bound layout assertions FAIL because the external-sync styles do not exist yet.

- [ ] **Step 3: Replace obsolete conversation layout styles**

Preserve shared `.draft-field`, `.draft-fieldset`, `.draft-segmented`, `.draft-entity-grid`, `.draft-data-summary`, and `.draft-error` rules because the manual form reuses them. Remove rules used only by `assistant-create-layout`, chat messages, composer, assistant state, and `task-draft-panel` after confirming with `rg` that no JSX references remain.

Add these layout foundations:

```css
.workspace-primary-actions {
  display: grid;
  gap: 7px;
  padding: 0 13px;
}

.workspace-agent-entry,
.workspace-new-task {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 0 12px;
  border-radius: 6px;
}

.workspace-agent-entry {
  border: 1px solid #dfe6e3;
  color: #77827e;
  background: #f5f7f6;
  cursor: not-allowed;
}

.workspace-command-copy {
  display: grid;
  gap: 1px;
  text-align: left;
}

.workspace-command-copy small {
  color: #929c98;
  font-size: 9px;
}

.external-sync-page {
  width: min(100% - 48px, 980px);
}

.sync-methods,
.manual-sync-form {
  border-top: 1px solid #dce4e1;
  background: #fff;
}

.sync-methods {
  padding: 24px;
}

.sync-method-grid,
.sync-attachments,
.sync-settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.sync-method {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  min-height: 78px;
  padding: 14px;
  border: 1px solid #d8e1de;
  border-radius: 7px;
  color: #35433f;
  background: #fff;
  text-align: left;
}

.sync-method.active {
  border-color: #4b9186;
  box-shadow: 0 0 0 2px rgba(15, 118, 110, 0.08);
}

.sync-method:disabled {
  color: #8b9591;
  background: #f5f7f6;
  cursor: not-allowed;
}

.manual-sync-form {
  padding: 24px;
}

.sync-attachment {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-height: 72px;
  padding: 12px;
  border: 1px solid #dbe4e1;
  border-radius: 6px;
  background: #f8faf9;
  cursor: pointer;
}

.sync-start-button {
  width: 100%;
  margin-top: 22px;
}
```

At `max-width: 720px`, set `.external-sync-page` to `width: min(100% - 28px, 980px)` and set `.sync-method-grid`, `.sync-attachments`, and `.sync-settings-grid` to one column. Keep stable padding of at least `16px` and ensure no horizontal scrolling.

Update collapsed-sidebar selectors so both primary commands keep stable icon dimensions and direct `title`/`aria-label` access when `.workspace-label` is hidden.

- [ ] **Step 4: Run full frontend verification**

Run:

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
npx playwright test tests/e2e/reconciliation-flow.spec.ts --grep "manual external data sync|collapses the desktop workspace|uses a drawer" --project=desktop --project=mobile
```

Expected: all commands exit `0`; the focused Playwright flows pass on desktop and Pixel 7 mobile projects.

- [ ] **Step 5: Perform browser visual verification**

Start `npm run dev:web` on an available port and inspect `/tasks`, `/tasks/new` before manual selection, `/tasks/new` after manual selection, and the created task detail at desktop `1440x900` and mobile `412x915`.

Verify:

- both workspace commands are readable and do not overlap history;
- disabled commands are visibly unavailable;
- CSV controls are absent before manual selection;
- the form stays within viewport bounds after selection;
- file names, validation errors, checkboxes, mode controls, and `开始同步` fit their containers;
- navigation to task detail retains the existing stage display.

- [ ] **Step 6: Commit Task 3**

```bash
git add frontend/src/styles/global.css frontend/tests/e2e/reconciliation-flow.spec.ts
git commit -m "test: verify external data sync workflow"
```

---

### Task 4: Final branch validation and contract check

**Files:**
- Review: `docs/superpowers/specs/2026-07-20-external-data-sync-ui-design.md`
- Review: all files changed since the feature branch base.

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: review-ready feature branch with no unrelated files and evidence for unit, lint, type, build, E2E, responsive, and OpenSpec compatibility.

- [ ] **Step 1: Confirm removed conversation UI has no live references**

Run:

```bash
rg -n "和 AI 一起新建对账|新建对账对话|任务草案|对账要求|assistant-create-layout|conversation-composer" frontend/src frontend/tests
```

Expected: no live JSX or test references. CSS references must also be removed unless another component still consumes them.

- [ ] **Step 2: Confirm required copy and routes**

Run:

```bash
rg -n "新建对话|即将开放|外部数据同步|手动同步|系统自动同步|开始同步" frontend/src frontend/tests
```

Expected: required copy appears in sidebar, page, and tests; `/tasks/new` remains the only synchronization creation route.

- [ ] **Step 3: Validate repository contracts and branch cleanliness**

Run:

```bash
openspec validate ai-new-ui
git diff --check
git status --short
```

Expected: OpenSpec validation succeeds, `git diff --check` reports no errors, and only intentional progress/review scratch files may remain ignored.

- [ ] **Step 4: Request task and whole-branch review**

Generate review packages from each recorded task base and from the feature branch merge base. Require no open Critical or Important findings before completion.

- [ ] **Step 5: Preserve the worktree for user inspection**

Do not merge or delete the branch automatically. Report the branch name, worktree path, commits, verification results, and local development URL.

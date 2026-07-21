# Agent-only Conversation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the visible task-draft workflow from “新建对话” so the route presents only an Agent conversation while preserving internal recognized context and the independent manual synchronization route.

**Architecture:** `ConversationCreatePage` continues to keep a private `TaskIntentDraft` for multi-turn assistant requests, but renders no draft fields and performs no session handoff or navigation. Manual CSV synchronization remains owned by `TaskCreatePage`; tests and OpenSpec are revised to make the two routes independent.

**Tech Stack:** React 19, TypeScript, React Router, Ant Design, Vitest, Testing Library, Playwright, OpenSpec.

## Global Constraints

- Do not implement Agent-triggered data discovery, automatic source selection, automatic synchronization, or task creation.
- Do not change backend APIs, migrations, durable Agent jobs, model behavior, or `/tasks/new` manual synchronization behavior.
- Preserve internal task-intent runtime validation and stale session-handoff cleanup.
- Preserve the current AI analysis and downstream reconciliation workflow.
- Do not stage or alter the user's uncommitted backend migration work.

---

### Task 1: Make the conversation page chat-only

**Files:**
- Modify: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/task-create/assistant.test.ts`
- Modify: `frontend/src/features/task-create/assistant.ts`

**Interfaces:**
- Consumes: `TaskCreationAssistant.respond(request: AssistantRequest): Promise<AssistantResponse>`, `clearTaskIntentDraft(): void`, and internal `TaskIntentDraft` state.
- Produces: a `/conversations/new` page with message history, pending feedback, textarea, and send action only.

- [ ] **Step 1: Replace draft-facing tests with chat-only assertions**

Update the initial rendering test to assert that the conversation exists and every manual configuration surface is absent:

```tsx
expect(screen.getByRole("region", { name: "新建对话" })).toBeInTheDocument();
expect(screen.queryByRole("region", { name: "任务草案" })).not.toBeInTheDocument();
expect(screen.queryByLabelText("任务名称")).not.toBeInTheDocument();
expect(screen.queryByLabelText("核对范围")).not.toBeInTheDocument();
expect(screen.queryByRole("button", { name: "继续外部数据同步" })).not.toBeInTheDocument();
expect(screen.queryByLabelText("选择三方系统 CSV")).not.toBeInTheDocument();
```

Replace field-editing and handoff tests with a multi-turn context test using a recording assistant. Assert that the second `respond` call receives the intent patch recognized during the first call. Update failure tests to assert `"没有理解这条要求，请换一种说法后重试。"` and remove all field assertions. Update the pending test to assert the composer textarea and send button are disabled until the deferred assistant response resolves.

Update assistant tests to expect Agent-oriented copy:

```tsx
expect(response.message).toContain("已记录同步需求");
expect(response.message).not.toContain("任务草案");
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: FAIL because the task-draft region, fields, and handoff action still render and the composer textarea is not disabled while collecting.

- [ ] **Step 3: Remove visible draft behavior from the component**

Keep private intent state and validated response merging:

```tsx
const [draft, setDraft] = useState<TaskIntentDraft>(() => createEmptyTaskIntentDraft());
const response = await assistant.respond({ draft, message });
const nextDraft = { ...draft, ...response.patch };
setDraft(nextDraft);
```

Remove `useNavigate`, `Button`, `Checkbox`, `ArrowRight`, `Sparkles`, entity label/type imports, `saveTaskIntentDraft`, `updateDraft`, `toggleType`, `continueToSync`, readiness calculations, and the complete `intent-draft-section` JSX. Keep `clearTaskIntentDraft()` in the mount effect.

Change conversation copy and pending behavior:

```tsx
text: "没有理解这条要求，请换一种说法后重试。"

{state === "collecting" && (
  <div className="assistant-thinking"><Spin size="small" /> 正在理解同步需求</div>
)}

<textarea disabled={state === "collecting"} ... />
```

Change deterministic assistant replies without changing their structured patches:

```ts
message: missingFields.length > 0
  ? `已记录同步需求，还需要补充${missingFields.join("和")}。`
  : `已记录${scope}的${typesLabel}同步需求。`
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx src/features/task-create/assistant.test.ts
```

Expected: both test files pass with no React warnings.

- [ ] **Step 5: Commit the chat-only component**

```bash
git add frontend/src/features/task-create/ConversationCreatePage.tsx frontend/src/features/task-create/ConversationCreatePage.test.tsx frontend/src/features/task-create/assistant.ts frontend/src/features/task-create/assistant.test.ts
git commit -m "feat: simplify new conversation to agent chat"
```

### Task 2: Reconcile styles, navigation flows, and OpenSpec

**Files:**
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/app/App.test.tsx`
- Modify: `frontend/tests/e2e/reconciliation-flow.spec.ts`
- Modify: `openspec/changes/v2-ui/proposal.md`
- Modify: `openspec/changes/v2-ui/design.md`
- Modify: `openspec/changes/v2-ui/specs/conversational-task-creation/spec.md`
- Modify: `openspec/changes/v2-ui/specs/external-data-sync/spec.md`
- Modify: `openspec/changes/v2-ui/tasks.md`

**Interfaces:**
- Consumes: chat-only `/conversations/new`, independent manual `/tasks/new`, existing `sessionStorage` cleanup, and existing task-creation service.
- Produces: navigation and specifications that no longer promise a visible/editable conversational draft or conversation-to-sync handoff.

- [ ] **Step 1: Add shell and E2E assertions for independent routes**

Update application tests so “新建对话” asserts the chat heading and absence of `任务草案` and `继续外部数据同步` without changing task history.

Split the existing conversation-handoff Playwright flow into two observable behaviors:

```ts
test("keeps new conversation focused on agent chat", async ({ page }) => {
  await page.goto("/conversations/new");
  await page.getByLabel("对账目标").fill("只核对七年级的老师和学生");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/已记录.*同步需求/)).toBeVisible();
  await expect(page.getByText("任务草案", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "继续外部数据同步" })).toHaveCount(0);
});
```

Keep task creation coverage by entering `/tasks/new`, activating “手动同步”, selecting both synthetic CSV files, and asserting guarded creation and task-detail navigation. Do not rely on conversation handoff values in that test.

- [ ] **Step 2: Run affected tests and verify RED**

Run:

```bash
cd frontend
npm test -- --run src/app/App.test.tsx
PLAYWRIGHT_PORT=5199 npm run test:e2e -- --grep "conversation|manual external"
```

Expected: old handoff assertions fail until shell/E2E expectations are updated consistently.

- [ ] **Step 3: Remove conversation-draft-only CSS**

Delete selectors used exclusively by removed markup:

```css
.intent-draft-section
.intent-draft-heading
.intent-fields-grid
.intent-draft-action
```

When a selector is grouped with manual-sync selectors, remove only the `.intent-*` entry and preserve `.sync-*`, `.draft-field`, `.draft-fieldset`, `.draft-segmented`, `.draft-entity-grid`, and `.draft-data-summary` rules used by `TaskCreatePage`.

- [ ] **Step 4: Update V2 OpenSpec to the approved behavior**

Revise the artifacts to state:

```markdown
- “新建对话” currently presents only Agent messages and the composer.
- Recognized intent remains internal and is not displayed, directly edited, persisted for handoff, or submitted.
- The current conversation has no “继续外部数据同步” action.
- Manual synchronization remains an independent `/tasks/new` workflow.
- Agent-driven data discovery and automatic synchronization are future work and out of scope.
```

Remove the external-sync scenario that initializes from a conversation handoff. Add a completed task section documenting chat-only UI, removal of handoff expectations, CSS cleanup, and updated unit/E2E coverage.

- [ ] **Step 5: Run the complete verification suite**

Run sequentially to avoid Playwright and ESLint racing over `test-results/`:

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
PLAYWRIGHT_PORT=5199 npm run test:e2e
cd ..
openspec validate v2-ui
openspec validate optimize-ai-analysis-workflow
```

Expected: all commands exit `0`; Playwright's project-specific skips remain expected; no backend file is staged.

- [ ] **Step 6: Commit specifications and integration coverage**

```bash
git add frontend/src/styles/global.css frontend/src/app/App.test.tsx frontend/tests/e2e/reconciliation-flow.spec.ts openspec/changes/v2-ui
git commit -m "docs: align v2 ui with agent-only conversation"
```

# Chat confirmation and message style implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cluttered start-confirmation content with two uniform Chinese metadata rows and align conversation messages with the Codex dark-text and neutral-gray visual system.

**Architecture:** Keep the existing conversation state and API objects unchanged. Add two presentation-only helpers beside `ConversationCreatePage`, render semantic metadata rows from the existing `AgentIntent` and `AgentStartConfirmation`, and scope all visual changes to conversation selectors in `apple.css`.

**Tech Stack:** React, TypeScript, CSS, Vitest, Testing Library.

## Global constraints

- Do not change API contracts, task confirmation behavior, conversation state, or synchronization execution.
- Keep the existing `开始同步前确认` title and `确认开始同步` action.
- Local CSV sources display the basename of `source_ref`; remote CSV sources display `display_origin`.
- Entity types display only as `部门`, `教师`, and `学生`, in that fixed order.
- The title, metadata labels, metadata values, and action text use 13 px.
- Assistant and user message text use `var(--codex-ink)`.
- User message bubbles use `var(--codex-panel-muted)` and neutral gray borders and avatar colors.

---

### Task 1: Refine the confirmation card and message presentation

**Files:**
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`
- Modify: `frontend/src/styles/apple.css`
- Test: `frontend/src/styles/global.test.ts`

**Interfaces:**
- Consumes: `AgentIntent.source`, `AgentStartConfirmation.entity_types`, and the existing `startTask()` handler.
- Produces: `confirmationSourceLabel(intent)` returning a safe visible source label and `confirmationEntities(entityTypes)` returning fixed-order Chinese labels.

- [ ] **Step 1: Write failing component tests**

Extend the default confirmation fixture to include:

```typescript
intent: {
  title: "全校教师同步",
  entity_types: ["teacher"],
  source: { kind: "local", source_ref: "third-party/teacher-roster.csv" },
  target: { kind: "local", source_ref: "seewo/teacher-roster.csv" },
},
```

Before starting the task, assert:

```typescript
expect(screen.getByText("第三方对象")).toBeInTheDocument();
expect(screen.getByText("teacher-roster.csv")).toBeInTheDocument();
expect(screen.getByText("同步数据")).toBeInTheDocument();
expect(screen.getByText("教师")).toBeInTheDocument();
expect(screen.queryByText("可以开始同步。")).not.toBeInTheDocument();
expect(screen.queryByText("teacher")).not.toBeInTheDocument();
```

Update the remote CSV test to assert `data.example.test` beside `第三方对象` and that `第三方来源：data.example.test` is absent. Add a case whose entity input is `["student", "department", "teacher"]` and assert the visible value is exactly `部门、教师、学生`.

- [ ] **Step 2: Run the component test and verify it fails**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: FAIL because the card still renders the summary, `第三方来源`, and raw English entity types.

- [ ] **Step 3: Implement the presentation helpers and card markup**

Import `AgentEntityType` from `../../api/agent`, then add:

```typescript
const confirmationEntityOrder: AgentEntityType[] = ["department", "teacher", "student"];
const confirmationEntityLabels: Record<AgentEntityType, string> = {
  department: "部门",
  teacher: "教师",
  student: "学生",
};

function confirmationSourceLabel(intent?: AgentIntent) {
  const source = intent?.source;
  if (!source) return "已选择的第三方数据";
  if (source.kind === "remote_csv" && source.display_origin) {
    return source.display_origin;
  }
  if (source.source_ref) {
    return source.source_ref.split(/[\\/]/).filter(Boolean).at(-1)
      ?? source.source_ref;
  }
  return source.configuration_id ?? "已选择的第三方数据";
}

function confirmationEntities(entityTypes: AgentEntityType[]) {
  const selected = new Set(entityTypes);
  return confirmationEntityOrder
    .filter((entityType) => selected.has(entityType))
    .map((entityType) => confirmationEntityLabels[entityType])
    .join("、");
}
```

Replace the summary and `small` elements with:

```tsx
<dl className="start-confirmation-details">
  <div>
    <dt>第三方对象</dt>
    <dd>{confirmationSourceLabel(agentIntent)}</dd>
  </div>
  <div>
    <dt>同步数据</dt>
    <dd>{confirmationEntities(confirmation.entity_types)}</dd>
  </div>
</dl>
```

Keep the existing button and `startTask()` call unchanged.

- [ ] **Step 4: Write failing stylesheet assertions**

In `frontend/src/styles/global.test.ts`, assert that `apple.css` contains:

```typescript
expect(appleCss).toMatch(
  /\.apple-page \.conversation-message p\s*\{[^}]*color:\s*var\(--codex-ink\)/s,
);
expect(appleCss).toMatch(
  /\.apple-page \.conversation-message\.user\s*\{[^}]*border-color:\s*var\(--codex-border\)[^}]*background:\s*var\(--codex-panel-muted\)/s,
);
expect(appleCss).toMatch(
  /\.apple-page \.start-confirmation[^}]*font-size:\s*13px/s,
);
expect(appleCss).toMatch(
  /\.start-confirmation-details\s*\{[^}]*display:\s*grid/s,
);
```

- [ ] **Step 5: Run the stylesheet test and verify it fails**

Run:

```bash
cd frontend
npm test -- --run src/styles/global.test.ts
```

Expected: FAIL because the existing user message styles are blue and the metadata layout does not exist.

- [ ] **Step 6: Implement scoped Codex styling**

Use `var(--codex-ink)` for message names and bodies. Set the user bubble to `var(--codex-panel-muted)` with `var(--codex-border)`, and use neutral gray avatar colors.

Style `.start-confirmation` and its descendants at 13 px. Render `.start-confirmation-details` as a grid with 8 px row gaps; each row is a two-column grid with a stable label column, wrapping values, zero margins, and no font-size changes.

- [ ] **Step 7: Run focused tests**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx src/styles/global.test.ts
```

Expected: all selected tests pass, including the unchanged task-start submission assertion.

- [ ] **Step 8: Run frontend quality gates and visual checks**

Run:

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Inspect `/conversations/new` at the default desktop viewport and at 390 px width. Confirm the confirmation card contains two aligned rows, long values wrap, both message roles use dark text, the user bubble is neutral gray, and the outer page does not scroll.

- [ ] **Step 9: Commit**

Run:

```bash
git add docs/superpowers/plans/2026-07-29-chat-confirmation-and-message-style.md frontend/src/features/task-create/ConversationCreatePage.tsx frontend/src/features/task-create/ConversationCreatePage.test.tsx frontend/src/styles/apple.css frontend/src/styles/global.test.ts
git commit -m "fix: refine chat confirmation styling"
```

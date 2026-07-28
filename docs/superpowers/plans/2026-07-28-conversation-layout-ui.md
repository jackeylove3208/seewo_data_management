# Conversation Layout UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the AI conversation inside the application viewport, simplify its composer to one visual shell, and move three external-sync labels inside their cards.

**Architecture:** Preserve all React state and event handlers, changing only presentational markup and CSS. Use the existing application grid to constrain the conversation page, keep `conversation-messages` as the internal scroll owner, and retain native fieldset naming with visually hidden legends plus visible in-card titles.

**Tech Stack:** React 19, TypeScript, CSS, Vitest, Testing Library

## Global Constraints

- Modify frontend presentation only; do not change API calls, state transitions, message sending, form values, or task behavior.
- The visible conversation title is exactly `数据同步助手`.
- The outer conversation page does not scroll; messages and an overlong task rail scroll internally.
- The desktop task rail is top-aligned and no taller than `min(50dvh, 440px)`.
- External-sync fieldsets retain accessible names.

---

### Task 1: Viewport-contained conversation layout

**Files:**
- Modify: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`
- Modify: `frontend/src/styles/global.test.ts`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/styles/apple.css`

**Interfaces:**
- Consumes: Existing `ConversationCreatePage` state, handlers, `.conversation-*` classes, and `TaskStatusRail`.
- Produces: A visible `数据同步助手` heading and viewport-contained chat layout with `.conversation-messages` as the vertical scroll owner.

- [ ] **Step 1: Write failing component and CSS contract tests**

```tsx
expect(screen.getByRole("heading", { name: "数据同步助手" })).toBeInTheDocument();
expect(screen.queryByRole("heading", { name: "新建对话" })).not.toBeInTheDocument();
```

```ts
expect(globalCss).toMatch(
  /\.workspace-main:has\(>\s*\.conversation-create-page\)\s*\{[^}]*height:\s*100dvh[^}]*overflow:\s*hidden/s,
);
expect(globalCss).toMatch(/\.conversation-messages\s*\{[^}]*overflow-y:\s*auto/s);
expect(globalCss).toMatch(/\.conversation-composer textarea\s*\{[^}]*resize:\s*none[^}]*border:\s*0/s);
```

- [ ] **Step 2: Run tests and verify the new assertions fail**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx src/styles/global.test.ts
```

Expected: FAIL because the assistant heading and new layout contracts do not exist.

- [ ] **Step 3: Add the visible title without changing action behavior**

```tsx
<div className="conversation-page-actions">
  <h1 className="conversation-assistant-title">数据同步助手</h1>
  <button className="conversation-reset-button" type="button">
    ...
  </button>
</div>
```

Keep the existing button props, disabled state, title, and click handler unchanged.

- [ ] **Step 4: Constrain the page and simplify the composer**

```css
.workspace-main:has(> .conversation-create-page) {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  height: 100dvh;
  overflow: hidden;
}

.conversation-create-page {
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

.conversation-messages {
  min-height: 0;
  overflow-y: auto;
}

.conversation-workspace > .task-status-rail {
  align-self: start;
  height: auto;
  max-height: min(50dvh, 440px);
  overflow-y: auto;
}

.conversation-composer textarea {
  resize: none;
  border: 0;
  box-shadow: none;
  background: transparent;
}
```

Remove the surface viewport-height calculation and use grid sizing for the remaining
space. Keep the composer button and submit handler unchanged.

- [ ] **Step 5: Run the focused tests and verify they pass**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx src/styles/global.test.ts
```

Expected: PASS.

### Task 2: In-card external-sync titles

**Files:**
- Modify: `frontend/src/features/task-create/TaskCreatePage.test.tsx`
- Modify: `frontend/src/features/task-create/TaskCreatePage.tsx`
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/styles/apple.css`

**Interfaces:**
- Consumes: Existing source, target, and entity `fieldset` elements and their form controls.
- Produces: `.sync-setting-title` text inside each card while the visually hidden `legend` continues to name the group.

- [ ] **Step 1: Write the failing component assertion**

```tsx
expect(container.querySelectorAll(".sync-setting-title")).toHaveLength(3);
expect(screen.getByRole("group", { name: "同步对象" })).toBeInTheDocument();
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/TaskCreatePage.test.tsx
```

Expected: FAIL because no `.sync-setting-title` elements exist.

- [ ] **Step 3: Keep semantic legends and render titles in the card content**

```tsx
<fieldset className="draft-fieldset sync-setting-card">
  <legend className="sr-only">{label}连接方式</legend>
  <span className="sync-setting-title" aria-hidden="true">{label}连接方式</span>
  ...
</fieldset>
```

Apply the same pattern to `同步对象`. Do not modify any select, file input, checkbox,
or handler.

- [ ] **Step 4: Style the in-card title**

```css
.sync-setting-title {
  display: block;
  color: var(--v2-ink-soft);
  font-size: 11px;
  font-weight: 650;
}
```

- [ ] **Step 5: Run the focused test and verify it passes**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/TaskCreatePage.test.tsx
```

Expected: PASS.

### Task 3: Frontend quality gates and visual verification

**Files:**
- Verify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Verify: `frontend/src/features/task-create/TaskCreatePage.tsx`
- Verify: `frontend/src/styles/global.css`
- Verify: `frontend/src/styles/apple.css`

**Interfaces:**
- Consumes: The completed presentation changes from Tasks 1 and 2.
- Produces: A tested production bundle with no functional code changes.

- [ ] **Step 1: Run all frontend checks**

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: all commands exit 0.

- [ ] **Step 2: Inspect the page at desktop and narrow widths**

Verify that the outer page has no vertical scrollbar, the message stream scrolls, the
composer has one border, and all three synchronization titles sit inside card borders.

- [ ] **Step 3: Review the final diff for scope**

```bash
git diff --check
git diff --stat
git diff
```

Expected: only the listed frontend presentation files, tests, and approved plan documents
change; no API or state behavior changes.

- [ ] **Step 4: Commit**

```bash
git add docs frontend
git commit -m "fix: refine conversation and sync form layout"
```

# Compact conversation header implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the conversation page header footprint to roughly half its current visual height while preserving all behavior.

**Architecture:** Keep the existing React structure and adjust only scoped CSS measurements. Protect the desktop and mobile compact layout with stylesheet regression assertions.

**Tech Stack:** React, TypeScript, CSS, Vitest.

## Global constraints

- Do not change JSX, state management, API calls, task behavior, or reset behavior.
- Desktop values are 12 px page top padding, 32 px action-row height, 7 px workspace gap, and 15 px title text.
- Mobile values are 10 px vertical page padding and a 6 px workspace gap.

---

### Task 1: Compact the conversation header

**Files:**
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/styles/apple.css`
- Test: `frontend/src/styles/global.test.ts`

**Interfaces:**
- Consumes: the existing `.conversation-create-page`, `.conversation-page-actions`, `.conversation-assistant-title`, and `.conversation-reset-button` selectors.
- Produces: a smaller header with unchanged markup and interaction behavior.

- [ ] **Step 1: Write the failing stylesheet regression test**

Add assertions for the exact desktop and mobile layout values:

```typescript
expect(globalCss).toMatch(
  /\.conversation-create-page\s*\{[^}]*padding:\s*12px 0 14px/s,
);
expect(globalCss).toMatch(
  /\.conversation-page-actions\s*\{[^}]*min-height:\s*32px[^}]*margin-bottom:\s*7px/s,
);
expect(globalCss).toMatch(
  /\.conversation-page-actions \.conversation-assistant-title\s*\{[^}]*font-size:\s*15px/s,
);
expect(appleCss).toMatch(
  /\.conversation-reset-button\s*\{[^}]*min-height:\s*32px[^}]*padding:\s*0 10px/s,
);
expect(globalCss).toMatch(
  /@media \(max-width:\s*720px\)[\s\S]*\.conversation-create-page\s*\{[^}]*padding:\s*10px 0/s,
);
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
cd frontend
npm test -- --run src/styles/global.test.ts
```

Expected: FAIL because the current values are 28 px, 42 px, 14 px, 18 px, and 36 px.

- [ ] **Step 3: Apply the minimal scoped CSS change**

Use:

```css
.conversation-create-page {
  padding: 12px 0 14px;
}

.conversation-page-actions {
  min-height: 32px;
  margin-bottom: 7px;
}

.conversation-page-actions .conversation-assistant-title {
  font-size: 15px;
}

.conversation-reset-button {
  min-height: 32px;
  padding: 0 10px;
}
```

Set the mobile page padding to `10px 0` and the mobile action-row gap to `6px`.

- [ ] **Step 4: Run focused and full frontend verification**

Run:

```bash
cd frontend
npm test -- --run src/styles/global.test.ts
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: all checks pass.

- [ ] **Step 5: Visually verify and commit**

Inspect the conversation page at desktop and mobile widths, confirm the header is compact and the reset control remains usable, then commit:

```bash
git add docs/superpowers/specs/2026-07-28-compact-conversation-header-design.md docs/superpowers/plans/2026-07-28-compact-conversation-header.md frontend/src/styles/global.css frontend/src/styles/apple.css frontend/src/styles/global.test.ts
git commit -m "fix: compact conversation header"
```

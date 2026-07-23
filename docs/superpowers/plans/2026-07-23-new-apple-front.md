# New Apple Front Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 React 工作台统一为 Apple 风格的轻量玻璃质感界面，并让“新建对话”“外部数据同步”与任务概览共用同一套真实数据展示规则。

**Architecture:** 保留现有路由、API 调用、表单行为和可访问性语义，仅在 React 页面结构中补充视觉层级，并在 `global.css` 中新增一组 scoped Apple workspace tokens。首页指标只从 `/api/agent/history` 的现有 `issue_summary`、`operation_summary` 和任务状态派生，不新增伪造字段；后端需求另写书面契约。

**Tech Stack:** React 19, TypeScript, React Router, Ant Design, lucide-react, Vitest.

## Global Constraints

- 不改变任何按钮的功能、文案、位置和主次关系。
- 不在前端假装 API/数据库连接器已支持；未支持连接器仍显示“不支持”。
- 不修改后端代码；后端配合项写入 `docs/backend/new-apple-front-backend-notes.md`。
- 不把视觉占位数字接入正式页面；指标必须来自已有 API 或显示“暂无数据”。
- 保持窄屏导航、键盘焦点、表单提交和现有测试语义。

---

### Task 1: Establish Apple workspace design tokens

**Files:**
- Modify: `frontend/src/styles/global.css`
- Test: `frontend/src/styles/global.test.ts`

- [ ] **Step 1: Add token assertions** for Apple canvas, glass surfaces, accent gradient, and reduced-motion fallback selectors.
- [ ] **Step 2: Run `npm test -- --run src/styles/global.test.ts` and verify the new assertions fail.**
- [ ] **Step 3: Add scoped `.apple-*` tokens and motion-safe background layers without deleting existing selectors.**
- [ ] **Step 4: Run the focused test and verify it passes.**
- [ ] **Step 5: Commit `style: add apple workspace visual tokens`.**

### Task 2: Restyle persistent sidebar and mobile header

**Files:**
- Modify: `frontend/src/app/WorkspaceSidebar.tsx`
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/styles/global.css`
- Test: `frontend/src/app/WorkspaceSidebar.test.tsx`, `frontend/src/app/App.test.tsx`

- [ ] **Step 1: Preserve all existing navigation labels and routes in tests.**
- [ ] **Step 2: Run the sidebar/app tests and capture baseline behavior.**
- [ ] **Step 3: Add Apple glass classes, active-state gradients, status glow, and mobile header treatment while preserving collapse and focus behavior.**
- [ ] **Step 4: Run both test files and verify all existing assertions pass.**
- [ ] **Step 5: Commit `style: refresh apple workspace navigation`.**

### Task 3: Replace task overview placeholder metrics with real derived metrics

**Files:**
- Modify: `frontend/src/features/tasks/TaskListPage.tsx`
- Modify: `frontend/src/styles/global.css`
- Test: `frontend/src/features/tasks/TaskListPage.test.tsx`

- [ ] **Step 1: Add a fixture covering completed, processing, failed, and zero-task history items.**
- [ ] **Step 2: Run the focused task-list test and verify the metric expectations fail.**
- [ ] **Step 3: Derive “历史任务”, “已完成”, “待处理问题”, and “治理操作成功率” only from loaded task history; show “暂无数据” for empty aggregates and label the scope as currently loaded history.**
- [ ] **Step 4: Add the Apple card layout and animated accent background.**
- [ ] **Step 5: Run the focused test and verify it passes.**
- [ ] **Step 6: Commit `feat: add data-backed apple task overview`.**

### Task 4: Restyle new conversation and external sync flows

**Files:**
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/features/task-create/TaskCreatePage.tsx`
- Modify: `frontend/src/styles/global.css`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`, `frontend/src/features/task-create/TaskCreatePage.test.tsx`

- [ ] **Step 1: Add tests asserting existing button labels, disabled states, connector warnings, and task-start behavior remain unchanged.**
- [ ] **Step 2: Run both focused test files and verify baseline behavior.**
- [ ] **Step 3: Add matching Apple headings, glass conversation surface, gradient send button, connector cards, and progress/confirmation cards.**
- [ ] **Step 4: Add explicit “暂不支持真实 API/数据库连接” copy for connector kinds that are not executable, without changing existing submission guards.**
- [ ] **Step 5: Run both focused test files and verify they pass.**
- [ ] **Step 6: Commit `style: unify apple conversation and sync flows`.**

### Task 5: Document backend coordination contract

**Files:**
- Create: `docs/backend/new-apple-front-backend-notes.md`

- [ ] **Step 1: Document current frontend-consumed fields and endpoint mappings.**
- [ ] **Step 2: Document optional dashboard aggregation endpoint, date-range semantics, connector capability metadata, and loading/error states.**
- [ ] **Step 3: Document acceptance criteria: no fabricated metrics, unsupported connectors visible, and stable pagination semantics.**
- [ ] **Step 4: Review the document for explicit field names, response examples, and no placeholders.**
- [ ] **Step 5: Commit `docs: describe backend needs for apple frontend`.**

### Task 6: Full verification

**Files:**
- Modify: `frontend/src/styles/global.test.ts` if needed for stable assertions.

- [ ] **Step 1: Run `npm test -- --run`.**
- [ ] **Step 2: Run `npm run lint`.**
- [ ] **Step 3: Run `npm run typecheck`.**
- [ ] **Step 4: Run `npm run build`.**
- [ ] **Step 5: Inspect `git diff --check` and worktree status.**

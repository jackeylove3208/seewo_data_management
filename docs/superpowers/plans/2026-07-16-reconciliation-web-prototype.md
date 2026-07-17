# Reconciliation Web Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a runnable React prototype for real paired CSV ingestion and a synthetic, independently selectable difference drill-down.

**Architecture:** A Vite single-page application uses React Router for the four approved routes, TanStack Query for server state, Ant Design for accessible controls, and small feature modules for task storage, uploads, task detail, and differences. Existing FastAPI ingestion endpoints provide real data; a typed demo repository fills only the unfinished difference contract.

**Tech Stack:** React, TypeScript, Vite, React Router, TanStack Query, Ant Design, Lucide React, Papa Parse, Vitest, Testing Library, ESLint.

## Global Constraints

- Keep the frontend operational and compact rather than marketing-oriented.
- Do not display hashes, snapshot IDs, mapping versions, schema versions, raw JSON, or AI provenance.
- Third-party data is authoritative and Mofa data is the target in every label and color treatment.
- A single person can have multiple independently selected field-level issues.
- Demo differences must be visibly distinguished from live backend facts.
- Do not claim that the prototype executes governance mutations.

---

### Task 1: Frontend foundation and application shell

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/eslint.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/providers.tsx`
- Create: `frontend/src/styles/global.css`
- Test: `frontend/src/app/App.test.tsx`

**Interfaces:**
- Produces: `App`, shared router/query/theme providers, and scripts `dev`, `build`, `test`, `lint`, `typecheck`.

- [x] Write `App.test.tsx` asserting the shell renders “魔方数据治理” and a task-history navigation destination.
- [x] Run `npm test -- --run src/app/App.test.tsx` and verify it fails because the app does not exist.
- [x] Add the Vite/React configuration and minimal provider-backed shell.
- [x] Run the focused test and verify it passes.

### Task 2: Typed data contracts, demo repository, and task history

**Files:**
- Create: `frontend/src/types/domain.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/ingestion.ts`
- Create: `frontend/src/data/demoDifferences.ts`
- Create: `frontend/src/data/taskHistory.ts`
- Create: `frontend/src/features/tasks/TaskListPage.tsx`
- Test: `frontend/src/features/tasks/TaskListPage.test.tsx`

**Interfaces:**
- Produces: `TaskHistoryItem`, `DifferencePerson`, `DifferenceIssue`, `getStoredTasks`, `saveStoredTask`, `ingestionApi`, and clickable task history rows.

- [x] Write a task-list test that clicks the body of a historical task row and expects navigation to `/tasks/:taskId`.
- [x] Run the focused test and verify the missing component failure.
- [x] Implement typed storage, demo data, and the task list with loading/empty/connection states.
- [x] Run the focused test and verify the clickable-row behavior passes.

### Task 3: Paired upload and real task creation

**Files:**
- Create: `frontend/src/features/task-create/csvSummary.ts`
- Create: `frontend/src/features/task-create/TaskCreatePage.tsx`
- Test: `frontend/src/features/task-create/TaskCreatePage.test.tsx`
- Modify: `frontend/src/api/ingestion.ts`
- Modify: `frontend/src/app/App.tsx`

**Interfaces:**
- Consumes: `ingestionApi.upload`, `ingestionApi.listMappings`, `ingestionApi.createTask`, and `saveStoredTask`.
- Produces: paired source-role uploads, local entity counts, entity-type scope selection, and redirect to a real task detail.

- [x] Write a test selecting two CSV files and asserting task creation stays disabled until an entity type is selected.
- [x] Run the focused test and verify it fails because the page is missing.
- [x] Implement Papa Parse summaries, paired upload controls, scope checkboxes, Chinese error messages, and the real API mutation.
- [x] Run the focused test and verify selection controls submission eligibility.

### Task 4: Task detail and hierarchical difference selection

**Files:**
- Create: `frontend/src/features/task-detail/TaskDetailPage.tsx`
- Create: `frontend/src/features/differences/selection.ts`
- Create: `frontend/src/features/differences/DifferenceCategoryPage.tsx`
- Create: `frontend/src/components/BackButton.tsx`
- Test: `frontend/src/features/differences/selection.test.ts`
- Test: `frontend/src/features/differences/DifferenceCategoryPage.test.tsx`
- Modify: `frontend/src/app/App.tsx`

**Interfaces:**
- Produces: `toggleCategory`, `togglePerson`, `toggleIssue`, tri-state derivation, category drill-down, independently expandable people, and issue confirmation.

- [x] Write pure selection tests proving category selection selects all issues, a person can be partially selected, and one issue can be deselected without clearing sibling issues.
- [x] Run the pure selection tests and verify they fail because the helpers are missing.
- [x] Implement the minimal immutable selection helpers and verify the pure tests pass.
- [x] Write a component test expanding a person with two problems, selecting only one, and expecting “已选择 1 人，共 1 个问题”.
- [x] Run the component test and verify the missing page failure.
- [x] Implement task summary rows, drill-down filters, expandable comparisons, fixed selection bar, confirmation dialog, and history-aware back buttons.
- [x] Run both focused suites and verify all hierarchical selection behavior passes.

### Task 5: Responsive polish and full verification

**Files:**
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/.env.example`
- Modify: `openspec/changes/demo/tasks.md`

**Interfaces:**
- Consumes: all implemented routes and test suites.
- Produces: desktop/mobile layouts, documented proxy configuration, and accurate OpenSpec task status.

- [x] Run `npm test -- --run`, fix only verified failures, and confirm zero failed tests.
- [x] Run `npm run lint`, `npm run typecheck`, and `npm run build`, confirming each exits zero.
- [x] Start FastAPI with SQLite and start Vite on an available port.
- [x] Inspect `/tasks`, `/tasks/new`, `/tasks/demo-001`, and `/tasks/demo-001/differences/teacher` in the browser at desktop and mobile widths.
- [x] Click a historical row, use the back button, expand a person, select one of multiple issues, and confirm the action count remains exact.
- [x] Mark only OpenSpec tasks fully satisfied by this implementation as complete.

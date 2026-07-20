## 1. Establish the draft handoff boundary

- [x] 1.1 Add focused tests for non-file task-intent validation, versioned session handoff, invalid stored payload fallback, explicit new-conversation reset, and successful-task cleanup
- [x] 1.2 Split the current task draft types into reusable task-intent fields and manual-sync attachment state without changing the existing task creation service contract
- [x] 1.3 Implement the versioned `sessionStorage` handoff adapter with runtime validation and no CSV or file-summary persistence
- [x] 1.4 Update assistant and task-creation service tests to prove their existing recognition, upload-role, payload, history, and idempotency behavior still passes through the new type boundary

## 2. Build the independent conversation experience

- [x] 2.1 Replace current creation-page tests with focused conversation tests for draft updates, direct editing, missing-field validation, assistant failure recovery, absence of CSV controls, and disabled handoff
- [x] 2.2 Extract the AI conversation and editable task-intent draft into a dedicated conversation page that never uploads files or creates a reconciliation task
- [x] 2.3 Add the explicit “继续外部数据同步” handoff action that persists the latest valid draft and navigates to manual sync
- [x] 2.4 Verify a fresh “新建对话” session clears an older handed-off draft without mutating task history

## 3. Build manual external data sync

- [x] 3.1 Add external-data-sync page tests proving the initial page contains only “手动同步”, contains no automatic-sync text, and does not render CSV controls before activation
- [x] 3.2 Implement the external-data-sync entry state and reveal the continuous manual-sync form only after “手动同步” is activated
- [x] 3.3 Add tests for handed-off task information, direct-entry defaults, editable task fields, valid paired CSV summaries, invalid-file preservation, and form readiness
- [x] 3.4 Implement the manual-sync form with paired CSV selectors, task fields, entity checkboxes, full/partial segmented mode, validation feedback, and a stable “开始同步” action
- [x] 3.5 Reuse the existing task creation service with one idempotency key, pending-submit locking, failure-state preservation, history refresh, successful draft cleanup, and navigation to task detail
- [x] 3.6 Add a regression test proving a created sync task continues into the unchanged data-ingestion, entity-resolution, difference-detection, and AI-analysis presentation

## 4. Update shared navigation and routes

- [x] 4.1 Add sidebar tests for distinct “新建对话” and “外部数据同步” entries, route-current state, historical-task immutability, collapsed tooltips, accessible names, and mobile drawer closure
- [x] 4.2 Add the `/conversations/new` route, retain `/tasks/new` for external data sync, and update the workspace to render both primary commands with distinct Lucide icons
- [x] 4.3 Rename task-list and application navigation actions from “新建对账” to “外部数据同步” and update affected unit-test assertions
- [x] 4.4 Confirm browser-back, page-back, recent-history highlighting, connection status, sidebar persistence, and focus behavior remain unchanged

## 5. Apply the V2 enterprise visual system

- [x] 5.1 Define balanced neutral, conversation-blue, sync-green, warning, error, typography, spacing, border, focus, and stable-control semantic tokens without gradients or decorative effects
- [x] 5.2 Restyle the expanded and collapsed workspace so both primary commands, dense history, current states, status indicators, tooltips, and footer controls remain visually stable
- [x] 5.3 Build the conversation page as an unframed operational layout with restrained headings, readable message rhythm, an independent draft tool, and a clear handoff action
- [x] 5.4 Build the external-data-sync entry and manual form as a constrained single-column workflow with ordered source, settings, validation, and action hierarchy and no nested cards
- [x] 5.5 Add desktop and mobile responsive rules, stable dimensions, visible keyboard focus, direct accessible labels, reduced-motion handling, and safeguards against overflow or overlap
- [x] 5.6 Check shared token changes against task list, task detail, difference review, analysis modals, and existing status/error states and limit styles that cause unrelated regressions

## 6. Verify behavior and presentation

- [x] 6.1 Update Playwright coverage for desktop and mobile sidebar navigation, conversation draft handoff, manual-sync reveal, paired CSV creation, and navigation to the unchanged task stages
- [x] 6.2 Run frontend unit tests, ESLint, TypeScript type checking, and the production build and resolve all V2-related failures
- [x] 6.3 Run focused Playwright flows against the local frontend and backend using synthetic CSV fixtures and confirm no duplicate task is created during guarded submission
- [x] 6.4 Capture and inspect desktop and mobile screenshots of the sidebar, conversation page, sync entry, populated manual form, validation error, and task detail for blank content, overflow, overlap, clipping, and visual hierarchy
- [x] 6.5 Validate `v2-ui` with OpenSpec and confirm implementation changes do not modify backend API, migrations, automatic-sync behavior, or the four downstream processing stages

## 7. Simplify new conversation to Agent chat

- [x] 7.1 Replace conversation tests with chat-only assertions and retain validated intent only as private multi-turn context
- [x] 7.2 Remove visible task fields, entity controls, processing mode, and the “继续外部数据同步” action from “新建对话”
- [x] 7.3 Keep stale handoff cleanup for compatibility while removing current persistence and navigation behavior
- [x] 7.4 Separate Playwright coverage for Agent conversation and independent manual CSV synchronization
- [x] 7.5 Remove conversation-draft-only CSS and preserve all manual-sync field, attachment, and summary styles
- [x] 7.6 Update V2 specifications to mark Agent-driven data discovery and automatic synchronization as future work

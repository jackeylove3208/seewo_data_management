## 1. Establish Layout and Navigation Baselines

- [x] 1.1 Add or update component tests that capture the current task routes, historical task navigation, page-back behavior, and backend connection retry behavior
- [x] 1.2 Define shared sidebar task-summary and navigation view models using direct fields such as title, status, time, and issue count
- [x] 1.3 Extract reusable brand, connection-status, and task-history data access from the current top header and task list without changing visible behavior

## 2. Build the Left Workspace Components

- [x] 2.1 Implement the expanded `WorkspaceSidebar` structure with product identity, collapse control, “新建对账”, recent history, complete-history entry, and bottom connection status
- [x] 2.2 Implement recent history rows with route-derived current-task highlighting, status summaries, issue counts, and accessible names
- [x] 2.3 Implement desktop collapsed mode with stable icon-button dimensions, tooltips, persisted preference, and no text overflow
- [x] 2.4 Implement the narrow-screen workspace drawer with menu trigger, focus management, route-change close behavior, overlay, and explicit close control
- [x] 2.5 Add component tests for new-task navigation, history navigation, current-task highlighting, collapse persistence, mobile drawer behavior, and offline retry

## 3. Replace the Top Navigation with the Workspace Shell

- [x] 3.1 Refactor `AppLayout` into a two-column workspace shell that renders the shared sidebar beside the existing routed main content
- [x] 3.2 Preserve `/tasks`, `/tasks/new`, `/tasks/:taskId`, and difference-detail routes and derive sidebar selection from the current URL
- [x] 3.3 Update page width, main-region, table, and return-navigation styles so existing screens remain usable beside expanded and collapsed sidebars
- [x] 3.4 Keep `/tasks` as the complete history and statistics view while limiting the sidebar to concise recent records
- [x] 3.5 Remove the obsolete top-header markup and styles only after all existing routes pass layout and navigation tests

## 4. Extract the Existing Task Creation Service

- [x] 4.1 Move paired upload, CSV summary, idempotency, task request construction, local history update, and success navigation into a reusable task creation service or hook
- [x] 4.2 Define validated `TaskDraft`, attachment state, missing-field, and submission-result types without adding hash or snapshot fields to user-facing summaries
- [x] 4.3 Refactor the current form to use the extracted creation service and run its existing tests to prove backend API behavior is unchanged before replacing the UI
- [x] 4.4 Add focused tests for upload failures, validation failures, duplicate-submit prevention, safe retry, and successful history refresh

## 5. Build Conversational Task Creation

- [x] 5.1 Define the `TaskCreationAssistant` adapter contract for user messages, attachment summaries, structured draft patches, follow-up fields, and validated assistant messages
- [x] 5.2 Implement a deterministic assistant adapter for the first UI version and tests, leaving the future small-model backend behind the same interface
- [x] 5.3 Implement the conversation state machine for idle, collecting, needs-input, draft-ready, submitting, failed, and created states
- [x] 5.4 Build the `/tasks/new` conversation view with message history, composer, attachment command, pending response, recoverable error, and stable responsive dimensions
- [x] 5.5 Build the editable task confirmation area showing title, scope, entity types, mode, and direct attachment summaries
- [x] 5.6 Disable task creation until the structured draft is valid and require an explicit “创建对账” action before calling the extracted creation service
- [x] 5.7 On success, refresh sidebar history and navigate to the created task; on failure, preserve valid messages, attachments, and draft values for retry
- [x] 5.8 Handle requests for direct governance execution or rollback by directing users to the reviewed workflow without issuing mutation commands

## 6. Preserve Existing Governance Workflows

- [x] 6.1 Verify task detail still shows processing stages and problem-type comparison inside the new workspace shell
- [x] 6.2 Verify category-level selection and per-person per-problem selection remain stable when navigating through sidebar history and browser back
- [x] 6.3 Verify historical tasks remain clickable from both the sidebar and complete task list and retain their saved selection state
- [x] 6.4 Verify the current route set has no execution-history or rollback UI yet and that the creation assistant does not issue execution or rollback commands

## 7. Responsive, Accessibility, and Visual Verification

- [x] 7.1 Add keyboard and accessible-name checks for sidebar commands, history items, collapse control, drawer controls, conversation composer, attachments, and confirmation action
- [x] 7.2 Add Playwright coverage for desktop expanded/collapsed navigation, mobile drawer navigation, conversational draft confirmation, successful task creation, failure recovery, and page back
- [x] 7.3 Capture desktop and mobile screenshots and verify that sidebar, chat, tables, buttons, text, and dynamic states do not overlap or resize the layout unexpectedly
- [x] 7.4 Run frontend unit tests, lint, build, and end-to-end tests and record any backend fixture or model-adapter assumptions
- [x] 7.5 Validate the `leftworkarea` OpenSpec change and document that the Seewo host connector remains outside this implementation scope

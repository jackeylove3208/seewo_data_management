# External data sync UI design

## Goal

Replace the current combined AI conversation and task-draft page with a focused external-data-sync workflow. The existing CSV upload, task creation, processing stages, history, and difference review behavior remain unchanged after the user starts a sync.

## Scope

This change is frontend-only. It introduces no Agent, model adapter, external-system API, new ingestion contract, or backend workflow stage.

The left workspace presents two separate primary entries:

- `新建对话`: visible but unavailable, with an `即将开放` status. It does not navigate to a placeholder Agent page.
- `外部数据同步`: the active command that replaces the existing `新建对账` label and opens `/tasks/new`.

The task draft belongs to the future Agent conversation and is not displayed on the external-data-sync page. The existing `TaskDraft` structure may remain as internal form state so the current task-creation service can be reused.

## Navigation

The workspace keeps its current product identity, recent history, complete-history entry, connection status, collapse behavior, and mobile drawer behavior.

The primary command area contains `新建对话` followed by `外部数据同步`. The unavailable conversation command uses an accessible disabled state and cannot receive route-current styling. `外部数据同步` uses `/tasks/new` and receives the active state on that route.

Any task-list command that currently says `新建对账` is renamed to `外部数据同步` so the same action has one name throughout the application.

## External data sync page

The page title is `外部数据同步`. Its first screen shows two sync methods:

- `手动同步`: available and visually primary.
- `系统自动同步`: disabled and labelled `暂未开放`.

The CSV controls are not rendered until the user activates `手动同步`. This makes the source choice explicit before file selection.

After manual sync is selected, the page reveals one continuous operational form containing:

- third-party source CSV selection and readable row summary;
- Seewo target CSV selection and readable row summary;
- sync task name;
- reconciliation scope;
- entity-type checkboxes;
- `全量对账` and `指定范围` mode control;
- a primary `开始同步` command.

The page does not show chat messages, a message composer, assistant thinking states, Agent branding, or a panel titled `任务草案`.

## Data flow

Manual sync reuses the existing client-side CSV summary, paired upload, idempotency key, task request construction, local history refresh, and success navigation.

`开始同步` remains disabled until both CSV files are valid and all existing required task fields are complete. Activating it uploads both files, creates the reconciliation task, refreshes recent history, and navigates to `/tasks/:taskId`.

The task detail continues the existing sequence without modification:

1. Data ingestion
2. Entity resolution
3. Difference detection
4. AI analysis

No synchronization mode is added to the backend request. `手动同步` is a frontend entry choice for the existing CSV workflow.

## Error handling

An invalid CSV reports the error beside the affected file and preserves the valid file and other form values. A failed upload or task-creation request leaves the completed form available for an idempotent retry. While submission is pending, `开始同步` is disabled to prevent duplicate requests.

The unavailable conversation and automatic-sync controls cannot trigger network calls. Their visible status must make the limitation clear without using an error alert.

## Responsive and accessible behavior

Desktop uses an unframed, constrained single-column form rather than the previous chat-plus-sidebar grid. Mobile keeps the existing workspace drawer and stacks sync-method controls, file selectors, fields, and actions without horizontal overflow.

The sync method, file inputs, entity choices, mode controls, disabled future entries, submission state, and error messages have direct accessible names. Collapsed workspace commands retain tooltips and accessible labels.

## Testing

Use test-driven changes at these boundaries:

- workspace tests assert the separate unavailable `新建对话` entry, renamed `外部数据同步` route, current-route state, collapsed mode, and mobile close behavior;
- page tests assert CSV controls are initially absent, manual sync reveals them, automatic sync is unavailable, valid files and fields enable `开始同步`, failures preserve input, and duplicate submission is blocked;
- application tests assert navigation from a historical task opens the external-data-sync page;
- existing task-creation service tests continue to prove upload roles, payload construction, history refresh, and idempotency behavior;
- Playwright covers desktop and mobile navigation, manual-sync reveal, paired CSV creation, navigation to task detail, and the unchanged processing-stage display.

Run frontend unit tests, lint, type checking, production build, focused Playwright flows, and desktop/mobile visual checks before completion.

## Non-goals

- Implementing the new-conversation Agent or a model-backed assistant
- Persisting an independent task draft
- Connecting to a real external system or Seewo host API
- Implementing automatic or incremental synchronization
- Changing backend ingestion, entity resolution, difference detection, or AI analysis behavior

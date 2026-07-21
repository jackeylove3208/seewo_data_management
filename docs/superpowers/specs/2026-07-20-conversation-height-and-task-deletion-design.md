# Conversation Height and Task Deletion Design

## Goal

Expand the Agent conversation into a primary full-height workspace and allow operators to permanently delete real reconciliation tasks that have completed analysis but have never produced a governance proposal.

## Scope

This change covers two frontend improvements and one guarded backend capability:

- Make `/conversations/new` use the available main-content height instead of limiting the message area to roughly half of the viewport.
- Add task deletion commands to recent history and the full task-history page.
- Permanently delete eligible task data and stored files only after backend eligibility validation.

Agent-driven synchronization, governance execution, deletion of demo tasks, bulk deletion, undo, retention policies, and deletion of tasks that have ever produced a governance proposal are out of scope.

## Conversation Workspace

`ConversationCreatePage` keeps its current message and composer structure. CSS changes make the page a bounded vertical workspace:

- On desktop, the page consumes the available viewport height below the application shell. The conversation surface grows with the page, the message list fills remaining space, and the composer remains at the bottom.
- The message list scrolls internally once content exceeds the available height.
- Stable `min-height`, `max-height`, flex/grid tracks, and `100dvh`-based constraints prevent messages, pending feedback, and the composer from shifting the shell.
- On mobile, the page retains a useful minimum message height while allowing the browser viewport and on-screen keyboard to reduce the workspace without horizontal overflow.
- Existing typography, colors, accessible labels, pending state, and Agent behavior remain unchanged.

The target desktop result is a conversation that occupies most of the usable first viewport rather than stopping at the current `410px` message limit.

## Deletion Eligibility

The backend is the sole authority for deletion eligibility. A task is deletable only when all of the following are true:

- The task exists and belongs to the authenticated operator tenant.
- The task is a real backend task rather than frontend demo data.
- A workflow stage run exists with `stage="analysis"` and `status="succeeded"`.
- No `governance_proposals` record exists for the task, regardless of proposal status or source.

AI analysis results and recommendations embedded in an analysis result do not block deletion. Creating either an AI-derived or manually entered governance proposal permanently makes the task ineligible for this deletion workflow, even when the proposal has never executed.

Missing or cross-tenant tasks return `404`. Tasks without a successfully completed analysis stage return `409` with “任务尚未完成 AI 分析，不能删除”. Tasks with any governance proposal return `409` with “该任务已有治理方案，不能删除”. Repeating deletion after a successful deletion returns `404` and does not recreate or partially restore data.

## Backend Deletion Boundary

Add `DELETE /api/reconciliation-tasks/{task_id}` to the reconciliation task router. The route delegates to a focused task-deletion service that:

1. Loads and tenant-checks the task.
2. Checks for a successfully completed analysis workflow stage and then checks for any governance proposal before mutating data.
3. Collects task-owned storage paths that must be removed after database success.
4. Deletes task-owned database records in foreign-key-safe order inside one transaction.
5. Deletes the reconciliation task last.
6. Returns `204 No Content` only after the database operation succeeds.

Task-owned records include workflow runs, analysis jobs and work items, analysis results and provenance children, difference records, task-scoped mappings and embeddings, snapshot rows and issues, snapshots, source-file records, and other current task-scoped records discovered from the model schema. The implementation uses explicit repository/service operations rather than weakening global immutability protections for ordinary domain mutations.

Database deletion and filesystem deletion cannot share one atomic transaction. The service records paths before database deletion, commits the database operation, and then removes task-owned upload and quarantine files using idempotent cleanup. Missing files are treated as already cleaned. A filesystem cleanup failure is logged for operator follow-up but does not turn the already committed task deletion into a client-visible failure; tests must prove repeated cleanup is safe. No path supplied by a client is accepted.

## Frontend Data Flow

The ingestion API client adds a typed `deleteTask(taskId)` operation. On a successful `204` response, the frontend removes the task from `localStorage` through a new `removeStoredTask` helper and emits the existing task-history update event.

Deletion is exposed in two places:

- Recent-history rows in `WorkspaceSidebar` show a trash icon for real tasks. The link and delete button are siblings inside a stable row wrapper so interactive controls are not nested.
- Rows in `TaskListPage` show the same icon command in a dedicated action column. Demo tasks never show the command.

Selecting delete opens an Ant Design confirmation modal containing the task title and an irreversible-deletion warning. Confirming locks the action while the request is pending. Cancelling makes no change. A backend `409` displays “该任务已有治理方案，不能删除”; other failures use the existing readable API error behavior. The local task is removed only after backend success.

Both views refresh through `TASK_HISTORY_UPDATED_EVENT`. If a reusable deletion controller is introduced, it owns pending/error/confirmation state but not rendering. No optimistic deletion is used.

## Navigation Behavior

Deleting from the sidebar or task list leaves the user on the current safe page. If deletion is later exposed from a task-detail page, successful deletion must navigate to `/tasks`; adding detail-page deletion is not part of this change.

The sidebar remains usable when collapsed and on mobile. The delete command is hidden in the collapsed desktop sidebar, where the full task list remains the deletion surface. On mobile it remains directly accessible without overlapping task labels or drawer controls.

## Error Handling and Safety

- Backend eligibility is checked again in the same transaction used for deletion.
- Proposal existence always wins over frontend state or cached history.
- The frontend never removes local history on `404`, `409`, network failure, or server failure.
- Pending deletion prevents duplicate confirmation requests.
- Demo records are excluded before any API request.
- Tenant identity comes from `OperatorContext`; no tenant identifier is accepted from the client.
- Database and file paths are derived from persisted task-owned records only.

## Testing

Backend tests cover:

- Deleting a task with completed analysis and no governance proposal.
- Refusing deletion before AI analysis has completed successfully.
- Refusing deletion when any AI or manual governance proposal exists.
- Returning `404` for missing and cross-tenant tasks.
- Removing task-owned database records without affecting another task.
- Cleaning stored upload and quarantine files idempotently.
- Repeating a successful deletion safely.

Frontend tests cover:

- `removeStoredTask` updates storage and emits the history event.
- Demo tasks have no delete command.
- Confirmation cancel performs no request.
- Confirmation success removes the local task and refreshes both history views.
- `409` and network failures preserve the task and show readable feedback.
- Pending deletion prevents duplicate requests.
- Sidebar controls remain accessible and do not trigger navigation when deleting.

CSS and Playwright coverage verify that the conversation fills the available desktop and mobile workspace, its composer remains visible, messages scroll without overflow, and history delete controls do not overlap labels or navigation.

## Rollback

Frontend rollback removes the delete commands and restores the prior conversation height constraints. Backend rollback removes the DELETE route and service; no schema migration is required when deletion is implemented against existing task ownership relationships. Permanently deleted task data cannot be restored by rollback, which is why confirmation and proposal-based eligibility are mandatory.

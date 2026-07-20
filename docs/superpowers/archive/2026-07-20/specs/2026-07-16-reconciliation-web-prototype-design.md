# Reconciliation Web prototype design

## Goal

Build a small operational Web interface that makes the existing ingestion backend easy to verify and demonstrates the agreed difference-selection workflow without pretending unfinished governance APIs exist.

## Scope

The prototype has four routes:

- `/tasks`: clickable task history with a create button.
- `/tasks/new`: paired CSV upload, local summary, entity-type scope selection, and task creation.
- `/tasks/:taskId`: task progress, source-versus-target counts, and selectable difference categories.
- `/tasks/:taskId/differences/:entityType`: people grouped under an entity type, expandable field-level differences, and independent issue selection.

The application shell shows the product name, a compact backend connection state, and no technical administration navigation.

## Data boundaries

Uploads, mapping discovery, task creation, task detail, readiness, and quarantine download use the existing FastAPI endpoints. Created task references are stored in the browser so they can appear in history while the backend lacks a task-list endpoint.

Difference categories, people, field comparisons, and recommendations use clearly labelled synthetic demo data because those backend endpoints do not exist yet. The processing action opens a selection summary but does not claim to mutate target data.

The UI does not expose hashes, snapshot IDs, schema versions, mapping versions, model names, prompt versions, raw JSON, or idempotency keys.

## Interaction design

### Task history

Every task row is clickable. It shows only creation time, file names, state, accepted counts, and issue count. Opening a task uses normal browser navigation. The task detail back button uses history navigation so list scroll and filters are retained.

### Create task

Two fixed upload surfaces distinguish the authoritative third-party source from the Mofa target. After both files are selected, the page displays simple record counts and an entity-type checklist. At least one entity type must be selected before task creation.

When the backend is available, the application uploads each file with its source role and creates a real reconciliation task. Server validation failures are translated into direct Chinese messages.

### Task detail

The detail page shows a short stage indicator and a comparison table. Each entity-type row contains a tri-state checkbox, source count, target count, issue count, status, and drill-in affordance. Selecting a category selects all currently eligible child issues; selecting only some children leaves the category checkbox indeterminate.

### Difference drill-down

The category page filters issues by missing, redundant, attribute, and structure conflict. Each person row expands into field-level comparisons. Every issue has its own checkbox, authoritative value, Mofa value, plain-language recommendation, and risk label. A person can therefore have multiple independently selected issues.

The fixed bottom action bar reports exact selected people and issue counts. Processing opens a confirmation dialog that explicitly states the governance backend is not connected in this prototype.

## Visual direction

Use a quiet white and cool-gray operational surface with green for the authoritative source, blue for the Mofa target, amber for attention, and red only for destructive or failed states. Layouts are unframed except for upload tools and repeated task rows. Tables remain readable at desktop widths and collapse into labelled rows on mobile.

## Error and empty states

- Backend unavailable: show a persistent connection banner while demo history and demo differences remain inspectable.
- Upload failure: keep both file selections and show the server-provided reason near the submit action.
- No history: show one direct create action.
- No selected issues: disable the processing action.
- Unknown task route: return to history with a concise not-found state.

## Verification

Vitest and Testing Library cover task-row navigation, category tri-state selection, independent issue selection, and disabled empty selection. A production build verifies TypeScript. Browser checks cover desktop and mobile rendering, full task drill-down, back navigation, and screenshot inspection.

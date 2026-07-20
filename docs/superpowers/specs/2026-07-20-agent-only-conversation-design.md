# Agent-only conversation design

## Context

The V2 reconciliation workspace currently renders a conversational assistant followed by a visible task-draft editor. The intended long-term direction is an intelligent data-sync agent that can infer scope and locate data without asking the operator to configure a manual task draft. Agent-driven synchronization is not implemented in this change.

## Goals

- Make `/conversations/new` a focused Agent conversation containing only message history, the pending indicator, the composer, and chat-level recovery feedback.
- Remove the visible task draft, editable task fields, entity controls, processing-mode controls, and external-sync handoff action from the conversation page.
- Keep the current internal task-intent state and validated assistant response boundary so future Agent work can build on recognized context.
- Keep manual external data sync, paired CSV upload, task creation, and downstream processing unchanged.

## Non-goals

- Do not implement Agent-triggered data discovery, automatic source selection, automatic synchronization, or task creation.
- Do not add backend APIs, connectors, migrations, durable Agent jobs, or new model behavior.
- Do not change `/tasks/new` or remove the independent manual synchronization workflow.

## Design

`ConversationCreatePage` will retain its internal `TaskIntentDraft` because the current assistant uses prior recognized values when processing later messages. The page will no longer expose that state as form controls and will no longer persist it through `sessionStorage` or navigate to external data sync.

The page will render one operational conversation surface. Assistant responses continue to update internal intent state and append messages. Incomplete information is requested through assistant messages rather than a visible draft footer. A malformed or failed assistant response preserves the prior internal state and appends a recoverable message asking the operator to describe the request again.

Starting a fresh conversation will continue to clear any stale handoff payload created by older application versions. This prevents an obsolete conversational draft from affecting a later direct visit to manual external data sync.

## Component changes

- Remove draft-form imports, event handlers, JSX, and conversation-to-sync navigation from `ConversationCreatePage`.
- Replace draft-oriented pending and error copy with Agent-oriented conversation copy.
- Remove CSS rules used only by the visible conversation draft while preserving shared manual-sync field styles.
- Keep `draftHandoff.ts` for external-sync compatibility and stale-payload cleanup.
- Update the V2 OpenSpec artifacts so the conversation is chat-only and has no current synchronization handoff.

## Testing

- Unit tests verify the page has no task-draft region, task fields, entity controls, CSV inputs, or external-sync handoff action.
- Conversation tests continue to cover assistant replies, malformed responses, failure recovery, pending-state locking of the composer, and stale handoff cleanup.
- Application and Playwright tests verify sidebar navigation opens the chat-only page while direct manual external sync remains unchanged.
- Run frontend unit tests, ESLint, TypeScript checking, production build, Playwright, and OpenSpec validation.

## Compatibility

No backend or API contract changes are required. Existing manual synchronization URLs and stored task history remain compatible. Older session handoff payloads are cleared when a new conversation starts and remain readable by direct manual sync until then.

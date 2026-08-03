# Hide successful DingTalk connection cards

## Goal

In the conversational task-creation flow, hide the DingTalk connection card as soon as saving and testing the connection succeeds. Keep the card visible when testing fails so the user can correct the configuration. Show a fresh card again only when a later conversation requests another DingTalk synchronization.

## Root cause

`ConversationCreatePage` renders the card whenever an `apiConnection` object exists. A successful test changes that object to the `active` state but does not remove it, so the form remains visible as a persistent “连接测试通过” card. Hydration can restore the same active object after a page reload.

## Design

Treat the connector state as the source of truth for card visibility:

- Render the connection card while its state still requires user attention, including initial configuration and failed validation.
- Do not render a connection whose state is `active`.
- Preserve the active connection in conversation state because its safe ID and metadata are still required to build the start confirmation and create the synchronization task.
- Do not change the backend response contract or connection lifecycle.

This makes successful dismissal durable across re-renders and page hydration. A later DingTalk request will return a new `configuration_required` connection, which satisfies the visibility condition and displays a new form.

## Error handling

An invalid or failed connection test continues to display the card and its sanitized error code. If the post-test conversation refresh fails after the connection has already become active, the global refresh error remains visible while the successful card stays hidden.

## Tests

Update the conversation-page regression coverage to verify:

- a failed connection test keeps the card visible;
- a successful connection test removes the card and reveals the start confirmation;
- hydrating an active task-scoped connection does not restore the card;
- a later response containing a fresh `configuration_required` connection shows the card again.

The component-level form tests remain unchanged because form submission and validation behavior do not change.

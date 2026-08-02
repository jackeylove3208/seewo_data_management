# Conversation interaction improvements

## Goal

Improve the Agent conversation page without changing the existing backend message API. Keep task progress visible before a task starts, make sending feel responsive, and add familiar keyboard behavior.

## Confirmed behavior

- Always reserve a `280px` right column for task status on desktop.
- Before a task exists, show the existing stages in a muted state with “等待创建任务”. When a task starts, activate the same rail in place.
- On narrow layouts, keep the existing responsive stacking and collapse behavior.
- Send with `Enter`; insert a newline with `Shift + Enter`.
- Do not submit while an IME composition session is active.
- Animate a newly submitted user message with a subtle `180ms` fade-and-rise transition.
- Keep the current JSON request/response API. After a successful response arrives, reveal only that new assistant reply with a frontend typewriter effect.
- Show welcome messages, restored history, and error messages immediately.
- Respect `prefers-reduced-motion` by displaying messages immediately without animation.

## Component design

`ConversationCreatePage` remains responsible for requests, conversation messages, task state, and confirmation cards. A focused `TypewriterText` component owns progressive text display and timer cleanup.

New messages may carry an ephemeral presentation marker. A locally submitted user message receives the entrance marker. A newly received assistant message receives the typewriter marker. Messages hydrated from the backend do not receive either marker, so refreshing the page never replays animations.

The typewriter component advances by Unicode characters at roughly `20–30ms` per step. It may reveal small groups for long replies to cap the total duration. Visual incremental text is hidden from assistive technology while the complete text is exposed once for announcement.

## Interaction flow

1. The user submits non-empty text with the send button or `Enter`.
2. The page immediately appends the user message and starts its entrance transition.
3. The composer remains disabled while the existing API request is pending.
4. On success, the page appends the complete assistant response with a typewriter marker.
5. The composer stays disabled until typing completes. Then response-derived confirmation UI appears and the conversation returns to its next state.
6. On failure, the error appears immediately and the composer returns to its existing retry state.

## Error and lifecycle handling

- Existing safe-link echoing and backend error messages remain unchanged.
- Starting a new conversation or unmounting the page cancels active typewriter timers.
- Repeated keydown events cannot submit while the composer is locked.
- IME composition confirmation never triggers submission.

## Testing

Add focused component tests that first fail for each new behavior:

- the idle page renders the task-status complementary region and a waiting label;
- active task progress still uses the same rail;
- `Enter` submits, `Shift + Enter` preserves a newline, and IME composition does not submit;
- a submitted user message receives the entrance-animation class;
- only a new successful assistant reply types progressively, then unlocks the composer and reveals confirmation UI;
- restored messages and errors render completely;
- reduced-motion mode bypasses animation.

Update CSS contract tests for the permanent two-column layout, waiting rail, message transition, and reduced-motion rule. Run the focused tests, full frontend unit suite, lint, typecheck, and production build.

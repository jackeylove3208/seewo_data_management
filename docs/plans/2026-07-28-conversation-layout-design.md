# Conversation layout design

## Scope

This change is limited to frontend presentation. It must not change conversation APIs,
task state, message sending, task progress, connector values, form submission, or any
other business behavior.

## Conversation page

- The workspace must occupy the visible application area without making the outer page
  scroll.
- The message stream remains vertically scrollable inside the conversation surface.
- The task status rail stays top-aligned and never exceeds half of the viewport height;
  it scrolls internally when its content is longer.
- The top-left visible title is `数据同步助手`; do not render a visible `新建对话`
  heading.
- Keep the existing `开启新对话` action and all of its behavior.
- The composer is one rounded shell. The textarea has no independent border, background,
  focus ring, or resize handle; the send button remains inside the shell.

## External synchronization page

- `三方系统连接方式`, `希沃魔方连接方式`, and `同步对象` appear inside their
  setting cards with normal spacing from the card border.
- Preserve `fieldset` and `legend` semantics for accessible group names.
- Do not change connector options, file inputs, checkbox behavior, or submission logic.

## Responsive behavior

- Desktop and mobile application shells remain viewport-contained.
- On narrow screens, the task rail follows the existing stacked behavior.
- Composer and setting cards must remain within their containers without horizontal
  overflow.

## Verification

- Add focused component assertions for the visible assistant title and in-card setting
  titles.
- Add stylesheet contract assertions for viewport containment, internal message
  scrolling, and the single-shell composer.
- Run the complete frontend unit suite, lint, typecheck, and production build.

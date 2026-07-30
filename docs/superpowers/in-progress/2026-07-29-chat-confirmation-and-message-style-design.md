# Chat confirmation and message style

## Scope

Refine the AI conversation confirmation card and message colors without changing task confirmation behavior, API contracts, conversation state, or synchronization execution.

## Start confirmation card

The card keeps the title `开始同步前确认` and the existing `确认开始同步` action. It removes the backend summary from this card so status-like copy such as `已确认` is not repeated.

The body contains exactly two metadata rows:

- `第三方对象`: show the basename of a local CSV `source_ref`; show `display_origin` for a remote CSV; show the connector configuration name for database or API sources; use `已选择的第三方数据` only when no safe source label is available.
- `同步数据`: translate `department`, `teacher`, and `student` to `部门`, `教师`, and `学生`, and display selected values in that fixed order.

The title, metadata labels, metadata values, and action text use a 13 px font. Weight and color, rather than font size, distinguish the title and labels. The two metadata rows use a compact two-column layout and wrap safely on narrow screens.

## Conversation messages

Both assistant and user message text use the existing Codex font stack and `var(--codex-ink)` for readable dark-gray text. User messages use `var(--codex-panel-muted)` as a light-gray bubble background with neutral gray borders and avatar colors. Assistant messages remain on the transparent page background.

## Accessibility and behavior

Existing accessible labels, message roles, button names, keyboard behavior, API calls, and task-start logic remain unchanged. Metadata stays readable when filenames, domains, or connection names wrap.

## Verification

Automated tests cover:

- removal of the confirmation summary from the card;
- local CSV filename extraction and remote CSV domain display;
- fixed Chinese entity labels and order with no English entity text;
- unchanged task-start submission;
- Codex dark message text and neutral-gray user bubble styling.

Visual checks cover desktop and 390 px layouts for alignment, wrapping, contrast, and preserved internal scrolling.

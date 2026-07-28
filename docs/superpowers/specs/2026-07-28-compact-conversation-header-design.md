# Compact conversation header

## Scope

Reduce the vertical space above the conversation without changing any conversation behavior, task state, reset behavior, or API contract.

## Design

The desktop conversation page uses 12 px top padding, a 32 px action row, and a 7 px gap before the conversation workspace. The “数据同步助手” title uses a 15 px font, while the reset control uses the same 32 px row height so it does not force the header taller.

On screens up to 720 px, the page uses 10 px vertical padding and a 6 px gap. Existing responsive behavior, including the icon-only reset button, remains unchanged.

## Verification

A focused stylesheet test locks the compact desktop and mobile measurements. The full frontend test, lint, typecheck, and build gates must remain green, followed by a visual desktop and mobile check.

# Conversation and sync layout fixes

## Scope

This change fixes four presentation and state-consistency problems without changing the Agent workflow:

1. Remove the large “新建对话” heading from the conversation content area while preserving a compact “开启新对话” action.
2. Align the “最近任务” heading and helper copy with the task rows inside the history card.
3. Stop showing a completed task’s inline progress card after the operator sends the next ordinary conversation message.
4. Make the four manual-sync settings use consistent cards and controls without overflowing labels or select text.

## Conversation layout

The conversation page uses a compact action bar containing only “开启新对话”. The message surface receives the reclaimed vertical space. The sidebar navigation entry remains named “新建对话”, and the conversation region keeps its accessible name.

## Terminal task boundary

The latest terminal task remains visible until the operator begins the next request. When a new ordinary message is accepted after a terminal task:

- the frontend immediately removes the old inline task progress card;
- the old task remains in history and is not deleted;
- the backend current-conversation response omits that terminal task once a later user message exists;
- page reload therefore cannot restore the stale completion card;
- starting the next task associates and displays the new task normally.

The backend determines this boundary by comparing the terminal run update time with conversation user-message creation times. Active runs are never hidden.

## Task history alignment

The history card owns the title-row padding. Its left and right insets match the task-row content, and the helper text remains within the rounded border at desktop and mobile widths.

## Manual sync settings

“任务名称”, “三方系统连接方式”, “希沃魔方连接方式”, and “同步对象” become consistent setting cards in a responsive two-column grid. Every text input and select uses `width: 100%`, `min-width: 0`, and safe text truncation. The grid collapses to one column on narrow screens.

## Verification

Automated tests cover:

- absence of the large conversation heading while retaining the reset action and accessible chat region;
- immediate and reload-safe removal of a stale terminal task;
- stable manual-sync setting-card structure;
- task-list title-row class and spacing contract;
- existing conversation, task history, and manual-sync behavior.

Visual verification checks desktop and narrow layouts for clipping, overflow, and usable message height.

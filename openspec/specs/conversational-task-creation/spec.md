# Conversational task creation Specification

## Purpose

Define the Agent-only conversation used to discuss a future reconciliation request without creating or configuring a file-backed task.

## Requirements

### Requirement: Provide an Agent-only conversation

The Web application SHALL provide an AI-style conversation that renders Agent messages, user messages, pending feedback, and a message composer without rendering task-draft fields, entity controls, processing-mode controls, CSV selectors, or a synchronization handoff command.

#### Scenario: User opens a new conversation

- **WHEN** the user navigates to “新建对话”
- **THEN** the page shows the Agent conversation and composer without a visible “任务草案” region or a “继续外部数据同步” action

#### Scenario: User describes a synchronization goal

- **WHEN** the user sends a message describing scope or entity types
- **THEN** the Agent responds in the conversation without navigating away, persisting a handoff, or exposing editable task configuration

### Requirement: Maintain private multi-turn context

The conversation SHALL retain validated recognized title, scope, entity types, and processing mode as private component state for subsequent Agent turns and SHALL NOT display, directly edit, persist, hand off, or submit that state in the current UI.

#### Scenario: User refines a previous request

- **WHEN** a later message relies on valid intent recognized in an earlier message
- **THEN** the assistant receives that recognized intent as context while the page continues to show only the conversation

#### Scenario: User starts a fresh conversation

- **WHEN** the user opens a new conversation while a legacy handoff payload exists
- **THEN** the application clears the stale payload without changing task history

### Requirement: Recover from assistant failures

The conversation SHALL preserve its prior validated internal context when assistant response validation or processing fails and SHALL allow another message after displaying a recoverable error.

#### Scenario: Assistant output is invalid

- **WHEN** the assistant adapter returns an invalid structured response
- **THEN** the conversation displays a recoverable error, keeps prior internal context unchanged, and re-enables the composer

#### Scenario: Assistant response is pending

- **WHEN** the assistant is processing a message
- **THEN** the page displays pending feedback and disables the textarea and send action until processing completes

### Requirement: Use the available conversation workspace

The conversation page SHALL use the available main-content viewport height, keep the composer visible at the bottom of the workspace, and scroll messages internally without horizontal overflow on desktop and mobile.

#### Scenario: User views a desktop conversation

- **WHEN** the user opens “新建对话” on a desktop viewport
- **THEN** the conversation surface expands beyond the compact message limit and the composer remains within the viewport

#### Scenario: User views a mobile conversation

- **WHEN** the user opens “新建对话” on a narrow viewport
- **THEN** messages scroll inside the surface and the composer remains visible without overlapping navigation or overflowing horizontally

# Successful DingTalk Connection Card Dismissal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide the conversational DingTalk connection card immediately after a successful connection test while retaining failed forms and showing a fresh form for the next DingTalk synchronization request.

**Architecture:** Keep `AgentApiConnectionCard` in page state as safe conversation metadata, but derive UI visibility from its lifecycle state. The page renders every connection state that still needs user attention and suppresses only `active`; backend payloads and connector state transitions remain unchanged.

**Tech Stack:** React 19, TypeScript 5.8, Vitest 3, Testing Library

## Global Constraints

- A successful connection test must hide the card immediately.
- An invalid or failed connection test must keep the form and sanitized error visible for retry.
- Hydrating an already-active connection must not restore the card.
- A later `configuration_required` response must show a fresh card.
- Do not change backend API contracts or connection lifecycles.
- Do not expose AppKey, AppSecret, tokens, provider response bodies, or headers.

---

### Task 1: Derive connection-card visibility from connector state

**Files:**
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx:850`
- Test: `frontend/src/features/task-create/ConversationCreatePage.test.tsx:466`

**Interfaces:**
- Consumes: `AgentApiConnectionCard.state`, whose union includes `configuration_required`, `pending`, `active`, `invalid`, and `disabled`.
- Produces: Page-level rendering behavior in which `active` connections remain in state but do not mount `ConversationApiConnectionCard`.

- [ ] **Step 1: Change the successful-submit regression assertion to require dismissal**

In `configures and tests an API connection without echoing credentials into chat`, keep the existing assertion that the invalid first attempt displays `connector_permission_denied`. Replace the successful-state assertion:

```tsx
expect(await within(card).findByText("连接测试通过")).toBeInTheDocument();
expect(await screen.findByLabelText("开始确认")).toBeInTheDocument();
```

with:

```tsx
await waitFor(() => {
  expect(screen.queryByLabelText("API 连接配置")).not.toBeInTheDocument();
});
expect(await screen.findByLabelText("开始确认")).toBeInTheDocument();
```

This same test continues proving that failure preserves the card and that credentials are never echoed.

- [ ] **Step 2: Add hydration and next-request regression tests**

Add one test that hydrates an active connection without a created task and verifies the card is absent:

```tsx
it("does not restore an active DingTalk connection card during hydration", async () => {
  render(<ConversationCreatePage agentApi={api({
    currentConversation: vi.fn().mockResolvedValue({
      id: "conversation-active-api",
      status: "active",
      messages: [],
      intent: {
        title: "钉钉教师同步",
        entity_types: ["teacher"],
        source: { kind: "api", configuration_id: "connection-active" },
      },
      api_connection: {
        provider_id: "dingtalk",
        state: "active",
        required_secret_fields: ["app_key", "app_secret"],
        connection_id: "connection-active",
        display_name: "当前钉钉连接",
        capabilities: { "entity.teacher.read": true },
        visibility_summary: { visible: true, teacher_count: 5 },
      },
      task: null,
    }),
  })} />);

  await waitForComposer();
  expect(screen.queryByLabelText("API 连接配置")).not.toBeInTheDocument();
});
```

Add a second test that starts from an active hidden connection, sends a later synchronization request, and returns a fresh `configuration_required` card:

```tsx
it("shows a fresh DingTalk connection card for a later synchronization request", async () => {
  const user = userEvent.setup();
  render(<ConversationCreatePage agentApi={api({
    currentConversation: vi.fn().mockResolvedValue({
      id: "conversation-next-api",
      status: "active",
      messages: [],
      api_connection: {
        provider_id: "dingtalk",
        state: "active",
        required_secret_fields: ["app_key", "app_secret"],
        connection_id: "connection-previous",
        display_name: "上一次钉钉连接",
        capabilities: { "entity.teacher.read": true },
        visibility_summary: { visible: true, teacher_count: 5 },
      },
      task: null,
    }),
    sendMessage: vi.fn().mockResolvedValue({
      accepted_message: "再次同步钉钉学生数据",
      message: "请填写本次钉钉连接信息。",
      intent: { title: "钉钉学生同步", entity_types: ["student"] },
      api_connection: {
        provider_id: "dingtalk",
        state: "configuration_required",
        required_secret_fields: ["app_key", "app_secret"],
        display_name: "新的钉钉临时连接",
        capabilities: {},
        visibility_summary: {},
      },
    }),
  })} />);

  await waitForComposer();
  expect(screen.queryByLabelText("API 连接配置")).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("对账目标"), "再次同步钉钉学生数据");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(await screen.findByLabelText("API 连接配置")).toBeInTheDocument();
  expect(screen.getByLabelText("连接名称")).toHaveValue("新的钉钉临时连接");
});
```

- [ ] **Step 3: Run the focused test and verify the new successful-dismissal assertions fail**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: FAIL because the existing render predicate mounts `ConversationApiConnectionCard` for `state: "active"`. The invalid-attempt assertion and fresh `configuration_required` assertion should still pass.

- [ ] **Step 4: Suppress only active connection cards**

In `ConversationCreatePage.tsx`, change the render predicate from:

```tsx
{apiConnection && conversationId && !task && (
```

to:

```tsx
{apiConnection
  && apiConnection.state !== "active"
  && conversationId
  && !task
  && (
```

Do not clear `apiConnection`: `refreshAfterApiConnection` must continue using the active connection while refreshing the current intent and start confirmation.

- [ ] **Step 5: Run focused tests and verify the behavior is green**

Run:

```bash
cd frontend
npm test -- --run src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: PASS with 52 tests and 0 failures.

- [ ] **Step 6: Run the frontend quality gates**

Run:

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

Expected: every command exits 0; Vitest reports all frontend tests passing; ESLint and TypeScript report no errors; Vite completes the production build.

- [ ] **Step 7: Inspect the final diff and commit the fix**

Run:

```bash
git diff --check
git diff -- frontend/src/features/task-create/ConversationCreatePage.tsx frontend/src/features/task-create/ConversationCreatePage.test.tsx
git status --short
git add frontend/src/features/task-create/ConversationCreatePage.tsx frontend/src/features/task-create/ConversationCreatePage.test.tsx
git commit -m "fix: dismiss successful DingTalk connection card"
```

Expected: the diff contains only the state-based render condition and focused regression coverage; the commit succeeds.

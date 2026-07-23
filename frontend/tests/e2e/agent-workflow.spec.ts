import { expect, test } from "@playwright/test";

const now = "2026-07-23T10:00:00Z";

function historyItem(overrides: Record<string, unknown>) {
  return {
    id: "agent-task",
    workflow_version: "new-agent-v1",
    task_kind: "sync",
    parent_task_id: null,
    phase: "terminal",
    status: "completed",
    title: "Agent 同步任务",
    report_id: "report-1",
    rollback_eligible: false,
    deletion_eligible: true,
    created_at: now,
    completed_at: now,
    issue_summary: { total: 1, excluded: 0 },
    operation_summary: { succeeded: 0, failed: 0, blocked: 0 },
    entity_types: ["student"],
    ...overrides,
  };
}

test("conversation Agent handles grouped approval, conflict dialogue, second confirmation and termination", async ({ page }) => {
  let clarified = false;
  let terminated = false;
  let approved = false;
  let confirmed = false;
  await page.route("**/api/agent/history*", (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route("**/api/agent/conversations", (route) => route.fulfill({ status: 201, json: { id: "conversation-1", status: "active" } }));
  await page.route("**/api/agent/conversations/conversation-1/messages", (route) => route.fulfill({ json: {
    message: "已生成全校学生同步计划。",
    intent: { title: "全校学生同步", entity_types: ["student"] },
    start_confirmation: { title: "全校学生同步", summary: "将锁定全校并启动 Agent", entity_types: ["student"] },
  } }));
  await page.route("**/api/agent/conversations/conversation-1/tasks", (route) => route.fulfill({ status: 202, json: {
    id: "task-1", workflow_version: "new-agent-v1", task_kind: "sync",
    phase: "analyze_batches", status: "running", title: "全校学生同步",
  } }));
  await page.route("**/api/agent/tasks/task-1/events*", (route) => {
    const events = terminated
      ? [{ id: "terminal", cursor: "4", type: "run.terminated", phase: "terminal", status: "terminated", payload: {}, created_at: now }]
      : clarified
        ? [{ id: "decision", cursor: "3", type: "clarification_decision_ready", phase: "clarify_identity_conflicts", status: "waiting_human", payload: { decision_id: "decision-1", summary: "按人工说明保留第一条记录" }, created_at: now }]
        : [
            { id: "approval", cursor: "1", type: "approval_required", phase: "aggregate_risk_and_approvals", status: "waiting_human", payload: { group_id: "group-1" }, created_at: now },
            { id: "conflict", cursor: "2", type: "clarification_required", phase: "clarify_identity_conflicts", status: "waiting_human", payload: { masked_evidence: "学生手机号 138****0001 存在身份冲突" }, created_at: now },
          ];
    return route.fulfill({ json: { cursor: events.at(-1)?.cursor ?? "0", events } });
  });
  await page.route("**/api/agent/tasks/task-1/approval-groups/group-1/approve", (route) => {
    approved = true;
    return route.fulfill({ json: { status: "approved" } });
  });
  await page.route("**/api/agent/tasks/task-1/clarification", (route) => {
    clarified = true;
    return route.fulfill({ json: { status: "interpreted" } });
  });
  await page.route("**/api/agent/tasks/task-1/clarification/decision-1/confirm", (route) => {
    confirmed = true;
    return route.fulfill({ json: { status: "confirmed" } });
  });
  await page.route("**/api/agent/tasks/task-1/terminate", (route) => {
    terminated = true;
    return route.fulfill({ json: { status: "terminated" } });
  });

  await page.goto("/conversations/new");
  await page.getByLabel("对账目标").fill("同步全校学生");
  await page.getByRole("button", { name: "发送" }).click();
  await page.getByRole("button", { name: "确认开始同步" }).click();

  await expect(page.getByText(/138\*{4}0001/)).toBeVisible();
  await page.getByRole("button", { name: "同意本组" }).click();
  expect(approved).toBe(true);
  await page.getByLabel("对账目标").fill("这是同一个学生，保留第一条");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("按人工说明保留第一条记录")).toBeVisible();
  await page.getByRole("button", { name: "确认解释" }).click();
  expect(confirmed).toBe(true);
  await page.getByRole("button", { name: "终止任务" }).click();
  await expect(page.getByRole("button", { name: "终止任务" })).toHaveCount(0, { timeout: 5_000 });
});

test("exclusive lock conflict remains visible and keeps the conversation retryable", async ({ page }) => {
  await page.route("**/api/agent/history*", (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route("**/api/agent/conversations", (route) => route.fulfill({ status: 201, json: { id: "conversation-lock", status: "active" } }));
  await page.route("**/api/agent/conversations/conversation-lock/messages", (route) => route.fulfill({ json: {
    message: "同步计划已准备。",
    intent: { title: "全校教师同步", entity_types: ["teacher"] },
    start_confirmation: { title: "全校教师同步", summary: "准备申请学校排他锁", entity_types: ["teacher"] },
  } }));
  await page.route("**/api/agent/conversations/conversation-lock/tasks", (route) => route.fulfill({
    status: 409,
    json: { detail: { code: "lock_conflict", message: "学校已有运行中的任务" } },
  }));

  await page.goto("/conversations/new");
  await page.getByLabel("对账目标").fill("同步全校教师");
  await page.getByRole("button", { name: "发送" }).click();
  await page.getByRole("button", { name: "确认开始同步" }).click();

  await expect(page.getByText("任务启动失败，现有需求仍然保留，可以重试。")).toBeVisible();
  await expect(page.getByRole("button", { name: "确认开始同步" })).toBeVisible();
});

test("history exposes abnormal and partial outcomes, protects mutations, and confirms an independent rollback", async ({ page }) => {
  let previewCalled = false;
  const items = [
    historyItem({ id: "abnormal-1", title: "异常输入报告", status: "failed", deletion_eligible: true, rollback_eligible: false, issue_summary: { total: 0, excluded: 3 } }),
    historyItem({ id: "partial-1", title: "部分执行任务", deletion_eligible: false, rollback_eligible: true, operation_summary: { succeeded: 1, failed: 1, blocked: 0 } }),
    historyItem({ id: "rollback-old", title: "历史回滚任务", task_kind: "rollback", parent_task_id: "partial-1", deletion_eligible: false }),
  ];
  await page.route("**/api/agent/history*", (route) => route.fulfill({ json: { items, next_cursor: null } }));
  await page.route("**/api/agent/tasks/abnormal-1", (route) => route.fulfill({ json: items[0] }));
  await page.route("**/api/agent/tasks/partial-1", (route) => route.fulfill({ json: items[1] }));
  await page.route("**/api/agent/tasks/rollback-new", (route) => route.fulfill({ json: {
    ...historyItem({ id: "rollback-new", title: "独立回滚任务", task_kind: "rollback", parent_task_id: "partial-1", phase: "plan_restore", status: "running" }),
  } }));
  await page.route("**/api/agent/tasks/*/events*", (route) => route.fulfill({ json: { cursor: "0", events: [] } }));
  await page.route("**/api/agent/tasks/abnormal-1", async (route) => {
    if (route.request().method() === "DELETE") return route.fulfill({ status: 204, body: "" });
    return route.fulfill({ json: items[0] });
  });
  await page.route("**/api/agent/tasks/partial-1/rollback-preview", (route) => {
    previewCalled = true;
    return route.fulfill({ status: 201, json: {
      task_id: "rollback-new", source_task_id: "partial-1", target_version_id: "version-1",
      operation_count: 1, requires_confirmation: true,
    } });
  });
  await page.route("**/api/agent/rollback-tasks/rollback-new/confirm", (route) => route.fulfill({ json: {
    id: "rollback-new", workflow_version: "new-agent-v1", task_kind: "rollback",
    parent_task_id: "partial-1", phase: "plan_restore", status: "running", title: "独立回滚任务",
  } }));

  await page.goto("/tasks");
  const taskList = page.locator(".task-list");
  await expect(taskList.getByRole("button", { name: /^异常输入报告 后端/ })).toBeVisible();
  await expect(taskList.getByRole("button", { name: /^部分执行任务 后端/ })).toBeVisible();
  await expect(taskList.getByRole("button", { name: "删除异常输入报告" })).toBeVisible();
  await expect(page.getByRole("button", { name: "删除部分执行任务" })).toHaveCount(0);
  await taskList.getByRole("button", { name: "删除异常输入报告" }).click();
  await page.getByRole("button", { name: "确认删除" }).click();
  await expect(taskList.getByRole("button", { name: /^异常输入报告 后端/ })).toHaveCount(0);

  await taskList.getByRole("button", { name: /^部分执行任务 后端/ }).click();
  await page.getByRole("button", { name: "创建回滚任务" }).click();
  await expect.poll(() => previewCalled).toBe(true);
  await page.getByRole("button", { name: "确认回滚" }).click();
  await expect(page).toHaveURL(/\/tasks\/rollback-new$/);
  await expect(page.getByText("回滚任务", { exact: true })).toBeVisible();
});

test("partial and abnormal facts are readable from the backend-owned report", async ({ page }) => {
  const task = historyItem({ id: "partial-report", title: "部分治理报告", rollback_eligible: true });
  await page.route("**/api/agent/history*", (route) => route.fulfill({ json: { items: [task], next_cursor: null } }));
  await page.route("**/api/agent/tasks/partial-report", (route) => route.fulfill({ json: task }));
  await page.route("**/api/agent/tasks/partial-report/events*", (route) => route.fulfill({ json: { cursor: "0", events: [] } }));
  await page.route("**/api/agent/tasks/partial-report/report", (route) => route.fulfill({ json: {
    id: "report-partial", task_id: "partial-report", kind: "sync", terminal_state: "partial",
    facts: {
      findings: [{ kind: "target_extra", category_zh: "希沃多余", analysis_zh: "权威数据中没有对应记录" }],
      excluded_findings: [{ reason: "第三方数据缺少编号" }],
      mutations: [{ id: "op-1", status: "succeeded", operation: "update", entity_kind: "student" }, { id: "op-2", status: "failed", operation: "delete", entity_kind: "student" }],
      mutation_summary: { succeeded: 1, failed: 1 },
    },
    content: {}, rollback_eligible: true, deletion_eligible: false, created_at: now,
  } }));

  await page.goto("/tasks/partial-report");
  await page.getByRole("button", { name: "查看任务报告" }).click();
  await expect(page.getByRole("heading", { name: "数据同步报告" })).toBeVisible();
  await expect(page.getByText("希沃多余")).toBeVisible();
  await expect(page.getByText("第三方数据缺少编号")).toBeVisible();
  await expect(page.getByText("partial", { exact: true })).toBeVisible();
});

import { expect, test } from "@playwright/test";

const now = "2026-07-23T10:00:00Z";

test("controlled graph progress and grouped approval recover after navigation", async ({ page }) => {
  let approved = false;
  const task = {
    id: "graph-task-1",
    workflow_version: "agent-graph-v1",
    task_kind: "sync",
    parent_task_id: null,
    phase: "aggregate_risk_and_approvals",
    status: "waiting_human",
    title: "全校学生数据同步",
    report_id: null,
    rollback_eligible: false,
    deletion_eligible: false,
    created_at: now,
    completed_at: null,
    issue_summary: { total: 50, excluded: 0 },
    operation_summary: { succeeded: 0, failed: 0, blocked: 0 },
    entity_types: ["student"],
  };
  await page.route("**/api/agent/history*", (route) =>
    route.fulfill({ json: { items: [task], next_cursor: null } }),
  );
  await page.route(/\/api\/agent\/tasks\/graph-task-1$/, (route) =>
    route.fulfill({
      json: approved
        ? { ...task, status: "running", phase: "compile_execution_plan" }
        : task,
    }),
  );
  await page.route(/\/api\/agent\/tasks\/graph-task-1\/events(?:\?.*)?$/, (route) =>
    route.fulfill({ json: { cursor: "0", events: [] } }),
  );
  await page.route(/\/api\/agent\/tasks\/graph-task-1\/graph$/, (route) =>
    route.fulfill({
      json: {
        task_id: task.id,
        workflow_version: "agent-graph-v1",
        graph_version: "agent-sync-graph-v1",
        graph_cursor: approved ? 9 : 8,
        current_node: approved
          ? "compile_execution_plan"
          : "wait_high_risk_approvals",
        business_stage: "governance_execution",
        current_action_zh: approved
          ? "正在编译治理执行计划"
          : "正在等待高风险操作审批",
        status: approved ? "running" : "waiting_human",
        can_terminate: true,
        human_gates: [
          {
            id: "gate-1",
            kind: "high_risk_approval",
            status: approved ? "approved" : "pending",
            item_count: 50,
          },
        ],
      },
    }),
  );
  await page.route(
    /\/api\/agent\/tasks\/graph-task-1\/graph\/gates\/gate-1\/decision$/,
    (route) => {
      approved = true;
      return route.fulfill({
        json: { gate_id: "gate-1", status: "approved", graph_cursor: 8 },
      });
    },
  );

  await page.goto("/tasks/graph-task-1");
  await expect(page.getByText(/共 50 条记录/)).toBeVisible();
  await expect(page.getByText("wait_high_risk_approvals")).toHaveCount(0);

  await page.getByRole("button", { name: "返回任务列表" }).click();
  const openNavigation = page.getByRole("button", { name: "打开导航" });
  if (await openNavigation.isVisible()) await openNavigation.click();
  await page
    .getByRole("region", { name: "最近任务" })
    .getByRole("link", { name: /全校学生数据同步/ })
    .click();
  await expect(page.getByText(/共 50 条记录/)).toBeVisible();

  await page.getByRole("button", { name: "同意" }).click();
  await expect(
    page.locator(".graph-live-progress").getByText("正在编译治理执行计划"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "同意" })).toHaveCount(0);
});

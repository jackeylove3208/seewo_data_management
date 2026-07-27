import { expect, test } from "@playwright/test";

const now = "2026-07-23T10:00:00Z";

test("controlled graph progress and grouped approval recover after navigation", async ({ page }) => {
  let approved = false;
  let submittedDecision: Record<string, unknown> | undefined;
  const approvalItems = [
    {
      finding_id: "finding-1",
      entity_kind: "student",
      entity_name: "李明",
      entity_number: "S-001",
      class_name: "三年级一班",
      source_locator: "csv:12",
      source_row_number: 12,
      operation_zh: "修改希沃中的学生记录",
      issue_zh: "手机号不一致",
      analysis_zh: "第三方权威手机号与希沃手机号不一致。",
      solution_zh: "将希沃手机号修改为第三方权威值。",
      changes: [
        {
          field: "phone",
          field_zh: "手机号",
          before: "138****1234",
          after: "139****5678",
        },
      ],
    },
    {
      finding_id: "finding-2",
      entity_kind: "student",
      entity_name: "王芳",
      entity_number: "S-002",
      class_name: "三年级二班",
      source_locator: "csv:18",
      source_row_number: 18,
      operation_zh: "修改希沃中的学生记录",
      issue_zh: "手机号不一致",
      analysis_zh: "第三方权威手机号与希沃手机号不一致。",
      solution_zh: "将希沃手机号修改为第三方权威值。",
      changes: [
        {
          field: "phone",
          field_zh: "手机号",
          before: "137****4321",
          after: "136****8765",
        },
      ],
    },
  ];
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
    issue_summary: { total: 2, excluded: 0 },
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
        termination_requested: false,
        human_gates: [
          {
            id: "gate-1",
            kind: "high_risk_approval",
            status: approved ? "approved" : "pending",
            risk: "high",
            cursor: 8,
            membership_hash: "a".repeat(64),
            item_count: approvalItems.length,
            entity_kind: "student",
            operation: "update",
            issue_kind: "field_difference",
            summary_zh: "修改 2 条学生手机号",
            risk_reason_zh: "学生手机号属于高危隐私字段。",
            actionable: true,
            unavailable_reason_zh: null,
            items: approvalItems,
          },
        ],
      },
    }),
  );
  await page.route(
    /\/api\/agent\/tasks\/graph-task-1\/graph\/gates\/gate-1\/decision$/,
    (route) => {
      submittedDecision = route.request().postDataJSON();
      approved = true;
      return route.fulfill({
        json: { gate_id: "gate-1", status: "approved", graph_cursor: 8 },
      });
    },
  );

  await page.goto("/tasks/graph-task-1");
  await expect(page.getByText(/共 2 条记录/)).toBeVisible();
  await expect(page.getByText("wait_high_risk_approvals")).toHaveCount(0);

  await page.getByRole("button", { name: "返回任务列表" }).click();
  const openNavigation = page.getByRole("button", { name: "打开导航" });
  if (await openNavigation.isVisible()) await openNavigation.click();
  await page
    .getByRole("region", { name: "最近任务" })
    .getByRole("link", { name: /全校学生数据同步/ })
    .click();
  await expect(page.getByText(/共 2 条记录/)).toBeVisible();

  await page.getByRole("button", { name: "同意" }).click();
  await expect.poll(() => submittedDecision).toEqual({
    decision: "approve",
    reason: "操作人确认高风险治理操作",
    approved_finding_ids: ["finding-1", "finding-2"],
    rejected_finding_ids: [],
    graph_cursor: 8,
    membership_hash: "a".repeat(64),
  });
  await expect(
    page.locator(".graph-live-progress").getByText("正在编译治理执行计划"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "同意" })).toHaveCount(0);
});

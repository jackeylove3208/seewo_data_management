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

function identityGraph(
  taskId: string,
  {
    submitted = false,
    confirmed = false,
  }: { submitted?: boolean; confirmed?: boolean } = {},
) {
  return {
    task_id: taskId,
    workflow_version: "agent-graph-v1",
    graph_version: "agent-sync-graph-v1",
    graph_cursor: 6,
    current_node: confirmed
      ? "analyze_actionable_batches"
      : "resolve_identity_conflicts",
    business_stage: "agent_analysis",
    current_action_zh: confirmed
      ? "正在分析可执行批次"
      : submitted
        ? "正在等待确认身份冲突选择"
        : "正在等待身份冲突选择",
    status: confirmed ? "running" : "waiting_human",
    can_terminate: true,
    termination_requested: false,
    human_gates: confirmed ? [] : [{
      id: "identity-gate-1",
      kind: "identity_conflict",
      status: "pending",
      item_count: 1,
      cursor: 5,
      actionable: true,
      conflicts: [{
        clarification_id: "clarification-1",
        status: submitted ? "interpreted" : "pending",
        summary_zh: "唯一身份字段命中了多个第三方权威候选，Agent 无法安全选择。",
        subject: {
          candidate_id: null,
          entity_kind: "student",
          category: "学生",
          name: "测试学生",
          number: "S-009",
          class_name: "一年级一班",
          phone_masked: "***0009",
          email_masked: "s***@example.test",
        },
        candidates: [
          {
            candidate_id: "candidate-1",
            entity_kind: "student",
            category: "学生",
            name: "测试学生",
            number: "S-001",
            class_name: "一年级一班",
            phone_masked: "***0001",
            email_masked: "s***@example.test",
          },
          {
            candidate_id: "candidate-2",
            entity_kind: "student",
            category: "学生",
            name: "测试学生二号",
            number: "S-002",
            class_name: "一年级二班",
            phone_masked: "***0002",
            email_masked: "s***@example.test",
          },
        ],
        allowed_outcomes: ["use_candidate", "target_extra"],
        interpretation_zh: submitted
          ? "你选择了第三方候选 A，确认后继续。"
          : null,
        operator_submission: submitted ? {
          decision: "select_candidate",
          selected_candidate_id: "candidate-1",
          note: "采用候选 A",
          interpretation_zh: "你选择了第三方候选 A，确认后继续。",
          submitted_at: now,
          source: "structured_selection",
        } : null,
      }],
    }],
  };
}

test("task detail keeps an in-flight identity choice read only after navigation", async ({ page }) => {
  let submitted = false;
  let confirmed = false;
  let selectionRequests = 0;
  let releaseSelection!: () => void;
  const selectionDelay = new Promise<void>((resolve) => {
    releaseSelection = resolve;
  });
  const task = historyItem({
    id: "identity-task",
    workflow_version: "agent-graph-v1",
    title: "身份冲突同步",
    phase: "clarify_identity_conflicts",
    status: "waiting_human",
    report_id: null,
    completed_at: null,
    deletion_eligible: false,
  });
  await page.route("**/api/agent/history*", (route) =>
    route.fulfill({ json: { items: [task], next_cursor: null } }),
  );
  await page.route(/\/api\/agent\/tasks\/identity-task$/, (route) =>
    route.fulfill({ json: task }),
  );
  await page.route("**/api/agent/tasks/identity-task/events*", (route) =>
    route.fulfill({ json: { cursor: "0", events: [] } }),
  );
  await page.route("**/api/agent/tasks/identity-task/graph", (route) =>
    route.fulfill({
      json: identityGraph("identity-task", { submitted, confirmed }),
    }),
  );
  await page.route(
    "**/api/agent/tasks/identity-task/clarifications/clarification-1/selection",
    async (route) => {
      selectionRequests += 1;
      expect(route.request().postDataJSON()).toMatchObject({
        decision: "select_candidate",
        selected_candidate_id: "candidate-1",
        note: "采用候选 A",
        graph_cursor: 6,
      });
      submitted = true;
      await selectionDelay;
      await route.fulfill({
        json: {
          decision_id: "clarification-1",
          status: "interpreted",
          task_id: "identity-task",
          decision: "select_candidate",
          selected_candidate_id: "candidate-1",
          interpretation_zh: "你选择了第三方候选 A，确认后继续。",
          requires_second_confirmation: true,
        },
      });
    },
  );
  await page.route(
    "**/api/agent/tasks/identity-task/clarification/clarification-1/confirm",
    (route) => {
      confirmed = true;
      return route.fulfill({ json: { status: "confirmed" } });
    },
  );

  await page.goto("/tasks/identity-task");
  await page.getByRole("radio", { name: "采用第三方候选 A" }).check();
  await page.getByLabel("补充说明（可选）").fill("采用候选 A");
  await page.getByRole("button", { name: "提交选择" }).click();

  await expect(page.locator(".identity-clarification-state")).toHaveText(
    /正在保存|等待确认/,
  );
  await expect(page.getByRole("button", { name: "提交选择" })).toHaveCount(0);
  await expect.poll(() => selectionRequests).toBe(1);

  await page.goto("/tasks");
  await page
    .getByRole("region", { name: "历史任务" })
    .getByRole("button", { name: /^身份冲突同步 / })
    .click();

  await expect(page.getByText("已选择：第三方候选 A")).toBeVisible();
  await expect(page.getByRole("button", { name: "提交选择" })).toHaveCount(0);
  await expect(page.getByRole("radio")).toHaveCount(0);
  releaseSelection();
  await expect(page.locator(".identity-clarification-state")).toHaveText("等待确认");
  await page.getByRole("button", { name: "确认选择并继续" }).click();

  expect(confirmed).toBe(true);
  await expect(
    page.getByText("正在分析可执行批次", { exact: true }).first(),
  ).toBeVisible();
});

test("conversation completes a structured identity conflict without leaving chat", async ({ page }) => {
  let submitted = false;
  let confirmed = false;
  await page.route("**/api/agent/history*", (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
  await page.route("**/api/agent/conversations/current", (route) =>
    route.fulfill({
      json: {
        id: "conversation-identity",
        status: "active",
        messages: [],
        task: {
          id: "conversation-identity-task",
          workflow_version: "agent-graph-v1",
          phase: "clarify_identity_conflicts",
          status: "waiting_human",
        },
      },
    }),
  );
  await page.route(/\/api\/agent\/tasks\/conversation-identity-task$/, (route) =>
    route.fulfill({
      json: {
        id: "conversation-identity-task",
        workflow_version: "agent-graph-v1",
        phase: "clarify_identity_conflicts",
        status: "waiting_human",
      },
    }),
  );
  await page.route(
    "**/api/agent/tasks/conversation-identity-task/events*",
    (route) => route.fulfill({ json: { cursor: "0", events: [] } }),
  );
  await page.route(
    "**/api/agent/tasks/conversation-identity-task/graph",
    (route) => route.fulfill({
      json: identityGraph("conversation-identity-task", { submitted, confirmed }),
    }),
  );
  await page.route(
    "**/api/agent/tasks/conversation-identity-task/clarifications/clarification-1/selection",
    (route) => {
      submitted = true;
      return route.fulfill({
        json: {
          decision_id: "clarification-1",
          status: "interpreted",
          task_id: "conversation-identity-task",
          decision: "select_candidate",
          selected_candidate_id: "candidate-2",
          interpretation_zh: "你选择了第三方候选 B，确认后继续。",
          requires_second_confirmation: true,
        },
      });
    },
  );
  await page.route(
    "**/api/agent/tasks/conversation-identity-task/clarification/clarification-1/confirm",
    (route) => {
      confirmed = true;
      return route.fulfill({ json: { status: "confirmed" } });
    },
  );

  await page.goto("/conversations/new");
  await expect(page.getByLabel("对账目标")).toBeDisabled();
  await page.getByRole("radio", { name: "采用第三方候选 B" }).check();
  await page.getByRole("button", { name: "提交选择" }).click();
  await expect(page.getByText("等待确认")).toBeVisible();
  await page.getByRole("button", { name: "确认选择并继续" }).click();

  expect(submitted).toBe(true);
  expect(confirmed).toBe(true);
  await expect(
    page.getByText("身份冲突选择已确认，Agent 正在继续处理。"),
  ).toBeVisible();
});

test("conversation Agent handles grouped approval, conflict dialogue, second confirmation and termination", async ({ page }) => {
  let clarified = false;
  let terminated = false;
  let approved = false;
  let confirmed = false;
  await page.route("**/api/agent/history*", (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route("**/api/agent/conversations/current", (route) =>
    route.fulfill({ json: null }),
  );
  await page.route("**/api/agent/conversations", (route) => route.fulfill({ status: 201, json: { id: "conversation-1", status: "active" } }));
  await page.route("**/api/agent/conversations/conversation-1/messages", (route) => route.fulfill({ json: {
    accepted_message: "同步全校学生",
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
  await page.route(/\/api\/agent\/tasks\/task-1$/, (route) =>
    route.fulfill({
      json: {
        id: "task-1",
        workflow_version: "new-agent-v1",
        task_kind: "sync",
        phase: terminated
          ? "terminal"
          : clarified
            ? "clarify_identity_conflicts"
            : "aggregate_risk_and_approvals",
        status: terminated ? "terminated" : "waiting_human",
        title: "全校学生同步",
      },
    }),
  );
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
  await page.route("**/api/agent/conversations/current", (route) =>
    route.fulfill({ json: null }),
  );
  await page.route("**/api/agent/conversations", (route) => route.fulfill({ status: 201, json: { id: "conversation-lock", status: "active" } }));
  await page.route("**/api/agent/conversations/conversation-lock/messages", (route) => route.fulfill({ json: {
    accepted_message: "同步全校教师",
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

test("a chat link shows only its cleaned origin and manual sync has no remote controls", async ({ page }) => {
  const submittedUrl = "https://data.example.test/roster.csv?secret=value";
  let taskRequest: Record<string, unknown> | undefined;
  await page.route("**/api/agent/history*", (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
  await page.route("**/api/agent/conversations/current", (route) =>
    route.fulfill({ json: null }),
  );
  await page.route("**/api/agent/conversations", (route) =>
    route.fulfill({
      status: 201,
      json: { id: "conversation-remote", status: "active" },
    }),
  );
  await page.route(
    "**/api/agent/conversations/conversation-remote/messages",
    (route) => route.fulfill({
      json: {
        accepted_message: "请同步 [远程CSV来源:data.example.test] 的学生",
        message: "已识别远程学生 CSV。",
        intent: {
          title: "远程学生同步",
          entity_types: ["student"],
          source: {
            kind: "remote_csv",
            remote_source_id: "remote-source-1",
            display_origin: "data.example.test",
          },
          target: { kind: "local", source_ref: "seewo/students.csv" },
        },
        start_confirmation: {
          title: "远程学生同步",
          summary: "将第三方学生 CSV 对齐到希沃数据。",
          entity_types: ["student"],
        },
      },
    }),
  );
  await page.route(
    "**/api/agent/conversations/conversation-remote/tasks",
    async (route) => {
      taskRequest = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({
        status: 202,
        json: {
          id: "task-remote",
          workflow_version: "agent-graph-v1",
          task_kind: "sync",
          phase: "ingest_and_normalize",
          status: "running",
          title: "远程学生同步",
        },
      });
    },
  );
  await page.route("**/api/agent/local-sources", (route) =>
    route.fulfill({ json: [] }),
  );

  await page.goto("/conversations/new");
  await page.getByLabel("对账目标").fill(`请同步 ${submittedUrl} 的学生`);
  await page.getByRole("button", { name: "发送" }).click();

  await expect(
    page.getByText("请同步 [远程CSV来源:data.example.test] 的学生"),
  ).toBeVisible();
  await expect(page.getByText("第三方来源：data.example.test")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("secret=value");
  await page.getByRole("button", { name: "确认开始同步" }).click();
  await expect.poll(() => taskRequest).toEqual({
    title: "远程学生同步",
    entity_types: ["student"],
    source: {
      kind: "remote_csv",
      remote_source_id: "remote-source-1",
    },
    target: { kind: "local", source_ref: "seewo/students.csv" },
  });

  await page.goto("/tasks/new");
  await page.getByRole("button", { name: "手动同步" }).click();
  await expect(page.getByLabel(/网页链接|远程链接/)).toHaveCount(0);
  await expect(page.getByRole("option", { name: /远程|网页/ })).toHaveCount(0);
});

test("history exposes abnormal and partial outcomes, protects mutations, and confirms an independent rollback", async ({ page }) => {
  let previewCalled = false;
  const items = [
    historyItem({ id: "abnormal-1", title: "异常输入报告", status: "failed", deletion_eligible: true, rollback_eligible: false, issue_summary: { total: 0, excluded: 3 } }),
    historyItem({ id: "partial-1", title: "部分执行任务", deletion_eligible: false, rollback_eligible: true, operation_summary: { succeeded: 1, failed: 1, blocked: 0 } }),
    historyItem({ id: "rollback-old", title: "历史回滚任务", task_kind: "rollback", parent_task_id: "partial-1", deletion_eligible: false }),
  ];
  await page.route("**/api/agent/history*", (route) => route.fulfill({ json: { items, next_cursor: null } }));
  await page.route("**/api/agent/tasks/partial-1", (route) => route.fulfill({ json: items[1] }));
  await page.route("**/api/agent/tasks/rollback-new", (route) => route.fulfill({ json: {
    ...historyItem({ id: "rollback-new", title: "独立回滚任务", task_kind: "rollback", parent_task_id: "partial-1", phase: "plan_restore", status: "running" }),
  } }));
  await page.route("**/api/agent/tasks/*/events*", (route) => route.fulfill({ json: { cursor: "0", events: [] } }));
  await page.route(/\/api\/agent\/tasks\/abnormal-1$/, async (route) => {
    if (route.request().method() === "DELETE") {
      return route.fulfill({ status: 204, body: "" });
    }
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
  await expect(page.getByRole("heading", { name: "数据同步分析报告" })).toBeVisible();
  await expect(page.getByText("希沃多余")).toBeVisible();
  await expect(page.getByText("第三方数据缺少编号")).toBeVisible();
  await expect(page.getByText("partial", { exact: true })).toBeVisible();
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { agentApi, type AgentGraphHumanGate } from "../../api/agent";
import { AgentTaskDetailPage } from "./AgentTaskDetailPage";

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AgentTaskDetailPage taskId="task-graph-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...rendered, client };
}

describe("controlled Agent graph task detail", () => {
  beforeEach(() => {
    vi.spyOn(agentApi, "task").mockResolvedValue({
      id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      task_kind: "sync",
      phase: "aggregate_risk_and_approvals",
      status: "waiting_human",
      title: "全校学生数据同步",
    });
    vi.spyOn(agentApi, "events").mockResolvedValue({ cursor: "0", events: [] });
    vi.spyOn(agentApi, "graph").mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 8,
      current_node: "wait_high_risk_approvals",
      business_stage: "governance_execution",
      current_action_zh: "正在等待高风险操作审批",
      sub_agent_zh: "治理执行 Agent",
      progress_completed: 3,
      progress_total: 5,
      status: "waiting_human",
      can_terminate: true,
      termination_requested: false,
      human_gates: [
        {
          id: "gate-1",
          kind: "high_risk_approval",
          status: "pending",
          risk: "high",
          cursor: 8,
          membership_hash: "a".repeat(64),
          item_count: 50,
          entity_kind: "student",
          operation: "update",
          issue_kind: "field_difference",
          summary_zh: "修改 50 条学生手机号",
          risk_reason_zh: "学生手机号属于高危隐私字段，本次操作会修改希沃目标中的手机号。",
          actionable: true,
          unavailable_reason_zh: null,
          items: [
            {
              finding_id: "finding-1",
              entity_kind: "student",
              entity_name: "李明",
              entity_number: "S-002",
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
          ],
        },
      ],
    });
    vi.spyOn(agentApi, "decideGraphGate").mockResolvedValue({
      gate_id: "gate-1",
      status: "approved",
      graph_cursor: 8,
    });
    vi.spyOn(agentApi, "decideGraphGates").mockResolvedValue({
      decisions: [],
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders business progress without internal English event identifiers", async () => {
    const { client } = renderPage();

    expect(await screen.findAllByText("正在等待高风险操作审批")).toHaveLength(2);
    expect(
      screen.getByRole("complementary", { name: "任务处理状态" }),
    ).toBeInTheDocument();
    expect(screen.getByText("报告生成")).toBeInTheDocument();
    expect(screen.getByText(/离开页面不会中断任务/)).toBeInTheDocument();
    expect(screen.getByText("治理执行 Agent · 3 / 5")).toBeInTheDocument();
    expect(screen.getByText(/共 50 条记录/)).toBeInTheDocument();
    expect(screen.getByText("修改 50 条学生手机号")).toBeInTheDocument();
    expect(
      screen.getByText("学生手机号属于高危隐私字段，本次操作会修改希沃目标中的手机号。"),
    ).toBeInTheDocument();
    expect(screen.getByText("修改学生：李明（编号 S-002）")).toBeInTheDocument();
    expect(screen.getByText("希沃第 12 行 · 三年级一班")).toBeInTheDocument();
    expect(screen.getByText("138****1234")).toBeInTheDocument();
    expect(screen.getByText("139****5678")).toBeInTheDocument();
    expect(screen.queryByText("wait_high_risk_approvals")).not.toBeInTheDocument();
    client.clear();
  });

  it("renders rollback-specific stages and the exact rollback action", async () => {
    vi.mocked(agentApi.task).mockResolvedValue({
      id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      task_kind: "rollback",
      parent_task_id: "source-task-1",
      phase: "approve_restore",
      status: "running",
      title: "独立回滚任务",
    });
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-rollback-graph-v1",
      graph_cursor: 7,
      current_node: "preflight_restore",
      business_stage: "report_and_rollback",
      current_action_zh: "正在准备逐项回滚，每项写入前都会重新校验",
      sub_agent_zh: "回滚执行 Agent",
      progress_completed: 2,
      progress_total: 3,
      status: "running",
      can_terminate: true,
      termination_requested: false,
      human_gates: [],
    });
    const { client } = renderPage();

    expect(
      await screen.findByText("读取并比对当前数据"),
    ).toBeInTheDocument();
    expect(screen.getByText("评估回滚影响")).toBeInTheDocument();
    expect(screen.getByText("确认回滚范围")).toBeInTheDocument();
    expect(screen.getByText("执行与验证")).toBeInTheDocument();
    expect(screen.getByText("生成回滚报告")).toBeInTheDocument();
    expect(screen.queryByText("数据接入")).not.toBeInTheDocument();
    expect(
      await screen.findAllByText("正在准备逐项回滚，每项写入前都会重新校验"),
    ).toHaveLength(2);
    client.clear();
  });

  it("shows a persisted rollback failure instead of an endless running state", async () => {
    vi.mocked(agentApi.task).mockResolvedValue({
      id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      task_kind: "rollback",
      phase: "plan_restore",
      status: "failed",
      title: "独立回滚任务",
      error: {
        code: "agent_action_contract_error",
        message: "当前阶段的数据校验失败，系统已停止自动重试。",
      },
    });
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-rollback-graph-v1",
      graph_cursor: 2,
      current_node: "load_verified_mutations",
      business_stage: "report_and_rollback",
      current_action_zh: "正在读取执行事实并比对当前目标数据",
      status: "failed",
      can_terminate: false,
      termination_requested: false,
      human_gates: [],
    });
    const { client } = renderPage();

    expect(
      await screen.findByText("回滚任务已停止自动重试"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("当前阶段的数据校验失败，系统已停止自动重试。"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/离开页面不会中断任务/)).not.toBeInTheDocument();
    client.clear();
  });

  it("shows each frozen rollback operation in the final approval", async () => {
    vi.mocked(agentApi.task).mockResolvedValue({
      id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      task_kind: "rollback",
      parent_task_id: "source-task-1",
      phase: "approve_restore",
      status: "waiting_human",
      title: "独立回滚任务",
    });
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-rollback-graph-v1",
      graph_cursor: 4,
      current_node: "wait_rollback_approval",
      business_stage: "report_and_rollback",
      current_action_zh: "正在等待确认回滚范围",
      progress_completed: 0,
      progress_total: 1,
      status: "waiting_human",
      can_terminate: true,
      termination_requested: false,
      human_gates: [
        {
          id: "rollback-gate-1",
          kind: "rollback_approval",
          status: "pending",
          item_count: 2,
          cursor: 3,
          summary_zh: "确认执行 2 条回滚操作",
          risk_reason_zh: "执行前仍会重新读取并校验当前目标数据。",
          actionable: true,
          items: [
            {
              finding_id: "operation-1",
              entity_kind: "student",
              entity_name: "李明",
              entity_number: "S-002",
              class_name: "三年级一班",
              source_locator: "database:seewo-mysql:student-1",
              operation_zh: "恢复同步修改的学生记录",
              issue_zh: "回滚同步修改",
              analysis_zh: "该记录属于本次冻结回滚范围。",
              solution_zh: "将手机号恢复为同步前的值。",
              changes: [
                {
                  field: "phone",
                  field_zh: "手机号",
                  before: "139****5678",
                  after: "138****1234",
                },
              ],
            },
            {
              finding_id: "operation-2",
              entity_kind: "student",
              entity_name: "王芳",
              entity_number: "S-003",
              class_name: "三年级二班",
              source_locator: "database:seewo-mysql:student-2",
              operation_zh: "删除同步新增的学生记录",
              issue_zh: "回滚同步新增",
              analysis_zh: "该记录属于本次冻结回滚范围。",
              solution_zh: "删除同步新增的学生记录。",
              changes: [
                {
                  field: "email",
                  field_zh: "邮箱",
                  before: "w***@example.test",
                  after: null,
                },
              ],
            },
          ],
        },
      ],
    });
    const { client } = renderPage();

    expect(
      await screen.findByRole("heading", { name: "确认执行 2 条回滚操作" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("查看具体操作（2 条）"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("恢复同步修改的学生记录：李明（编号 S-002）"),
    ).toBeInTheDocument();
    expect(screen.getByText("139****5678")).toBeInTheDocument();
    expect(screen.getByText("138****1234")).toBeInTheDocument();
    expect(
      screen.getByText("删除同步新增的学生记录：王芳（编号 S-003）"),
    ).toBeInTheDocument();
    expect(screen.getByText("w***@example.test")).toBeInTheDocument();
    client.clear();
  });

  it("dismisses a new rollback preview without rejecting its durable task", async () => {
    const user = userEvent.setup();
    vi.mocked(agentApi.task).mockResolvedValue({
      id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      task_kind: "sync",
      phase: "terminal",
      status: "completed",
      title: "已完成同步",
      rollback_eligible: true,
    });
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 20,
      current_node: "terminal",
      business_stage: "terminal",
      current_action_zh: "任务已结束",
      status: "completed",
      can_terminate: false,
      termination_requested: false,
      human_gates: [],
    });
    vi.spyOn(agentApi, "previewRollback").mockResolvedValue({
      task_id: "rollback-task-1",
      source_task_id: "task-graph-1",
      target_version_id: "version-1",
      operation_count: 8,
      state: "awaiting_confirmation",
      message_zh: "请确认是否创建独立回滚任务。",
      requires_confirmation: true,
    });
    const reject = vi.spyOn(agentApi, "rejectRollback");
    const confirm = vi.spyOn(agentApi, "confirmRollback");
    const { client } = renderPage();

    await user.click(await screen.findByRole("button", { name: "创建回滚任务" }));
    expect(
      await screen.findByRole("dialog", { name: "确认创建独立回滚任务？" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "暂不回滚" }));

    expect(
      screen.queryByRole("dialog", { name: "确认创建独立回滚任务？" }),
    ).not.toBeInTheDocument();
    expect(reject).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
    client.clear();
  });

  it("explains that an existing rollback already completed without replaying it", async () => {
    const user = userEvent.setup();
    vi.mocked(agentApi.task).mockResolvedValue({
      id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      task_kind: "sync",
      phase: "terminal",
      status: "completed",
      title: "已完成同步",
      rollback_eligible: true,
    });
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 20,
      current_node: "terminal",
      business_stage: "terminal",
      current_action_zh: "任务已结束",
      status: "completed",
      can_terminate: false,
      termination_requested: false,
      human_gates: [],
    });
    vi.spyOn(agentApi, "previewRollback").mockResolvedValue({
      task_id: "rollback-task-1",
      source_task_id: "task-graph-1",
      target_version_id: "version-1",
      operation_count: 8,
      state: "completed",
      message_zh: "该任务已完成回滚。",
      requires_confirmation: false,
    });
    const reject = vi.spyOn(agentApi, "rejectRollback");
    const confirm = vi.spyOn(agentApi, "confirmRollback");
    const { client } = renderPage();

    await user.click(await screen.findByRole("button", { name: "创建回滚任务" }));

    expect(
      await screen.findByRole("dialog", { name: "该任务已完成回滚" }),
    ).toBeInTheDocument();
    expect(screen.getByText("该任务已完成回滚。")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "查看回滚任务" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /关\s*闭/ }));

    expect(
      screen.queryByRole("dialog", { name: "该任务已完成回滚" }),
    ).not.toBeInTheDocument();
    expect(reject).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
    client.clear();
  });

  it("renders legacy Agent events as a Chinese blocked-state timeline", async () => {
    vi.mocked(agentApi.task).mockResolvedValue({
      id: "task-graph-1",
      workflow_version: "new-agent-v1",
      task_kind: "sync",
      phase: "analyze_batches",
      status: "blocked_model_error",
      title: "全校学生数据同步",
    });
    vi.mocked(agentApi.events).mockResolvedValue({
      cursor: "2",
      events: [
        {
          id: "event-1",
          cursor: "1",
          type: "model_attempt_failed",
          phase: "analyze_batches",
          payload: { attempt: 4, attempt_count: 4, failure_category: "model_timeout" },
          created_at: "2026-07-24T03:10:00Z",
        },
        {
          id: "event-2",
          cursor: "2",
          type: "model_retry_exhausted",
          phase: "analyze_batches",
          status: "blocked_model_error",
          payload: { attempt_count: 4 },
          created_at: "2026-07-24T03:10:01Z",
        },
      ],
    });
    const { client, container } = renderPage();

    expect(await screen.findByRole("heading", { name: "模型分析已暂停" })).toBeInTheDocument();
    expect(await screen.findByText("模型响应超时")).toBeInTheDocument();
    expect(screen.queryByText("analyze_batches")).not.toBeInTheDocument();
    expect(screen.queryByText("model_retry_exhausted")).not.toBeInTheDocument();
    expect(container.querySelector(".ant-progress")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "终止任务" })).toBeInTheDocument();
    client.clear();
  });

  it("never renders a running progress bar while a blocked event history is loading", async () => {
    vi.mocked(agentApi.task).mockResolvedValue({
      id: "task-graph-1",
      workflow_version: "new-agent-v1",
      task_kind: "sync",
      phase: "analyze_batches",
      status: "blocked_model_error",
      title: "全校学生数据同步",
    });
    vi.mocked(agentApi.events).mockResolvedValue({ cursor: "0", events: [] });
    const { client, container } = renderPage();

    expect(await screen.findByRole("heading", { name: "模型分析已暂停" })).toBeInTheDocument();
    expect(container.querySelector(".ant-progress")).not.toBeInTheDocument();
    client.clear();
  });

  it("shows the persisted graph failure category in the blocked notice", async () => {
    vi.mocked(agentApi.task).mockResolvedValue({
      id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      task_kind: "sync",
      phase: "analyze_batches",
      status: "blocked_model_error",
      title: "全校学生数据同步",
    });
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 12,
      current_node: "blocked_model_error",
      business_stage: "agent_analysis",
      current_action_zh: "模型分析已暂停",
      progress_completed: 0,
      progress_total: 1,
      status: "blocked_model_error",
      can_terminate: true,
      termination_requested: false,
      human_gates: [],
    });
    vi.mocked(agentApi.events).mockResolvedValue({
      cursor: "13",
      events: [
        {
          id: "event-blocked",
          cursor: "13",
          type: "run.blocked_model_error",
          phase: "analyze_batches",
          status: "blocked_model_error",
          payload: {
            attempt_count: 4,
            failed_node: "analyze_actionable_batches",
            failure_categories: ["tool_argument_rejected"],
          },
          created_at: "2026-07-24T03:10:01Z",
        },
      ],
    });
    const { client } = renderPage();

    expect(
      await screen.findAllByText(/工具参数未通过本批证据清单校验/),
    ).toHaveLength(2);
    expect(screen.queryByText(/达到四次尝试上限/)).not.toBeInTheDocument();
    client.clear();
  });

  it("submits one decision for the frozen homogeneous gate", async () => {
    const user = userEvent.setup();
    const { client } = renderPage();

    await user.click(await screen.findByRole("button", { name: "同意" }));

    expect(agentApi.decideGraphGate).toHaveBeenCalledWith(
      "task-graph-1",
      "gate-1",
      "approve",
      "操作人确认高风险治理操作",
      {
        approved_finding_ids: ["finding-1"],
        rejected_finding_ids: [],
        graph_cursor: 8,
        membership_hash: "a".repeat(64),
      },
    );
    expect(await screen.findByText("已允许")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "同意" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拒绝" })).not.toBeInTheDocument();
    client.clear();
  });

  it("defaults medium-risk items to approved but submits an exact mixed review", async () => {
    const user = userEvent.setup();
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 8,
      current_node: "wait_high_risk_approvals",
      business_stage: "governance_execution",
      current_action_zh: "正在等待治理操作确认",
      status: "waiting_human",
      can_terminate: true,
      termination_requested: false,
      human_gates: [
        {
          id: "gate-medium",
          kind: "high_risk_approval",
          status: "pending",
          risk: "medium",
          cursor: 8,
          membership_hash: "medium-membership-hash",
          member_decisions: {},
          item_count: 2,
          entity_kind: "student",
          operation: "update",
          issue_kind: "field_difference",
          summary_zh: "修改 2 条学生普通字段",
          risk_reason_zh: "该操作属于中风险变更，默认建议同意，但仍可逐项拒绝。",
          actionable: true,
          items: [
            {
              finding_id: "finding-a",
              entity_kind: "student",
              entity_name: "张三",
              entity_number: "S-001",
              class_name: "一班",
              source_locator: "csv:2",
              source_row_number: 2,
              operation_zh: "修改希沃中的学生记录",
              issue_zh: "姓名不一致",
              analysis_zh: "姓名需要按第三方权威值修正。",
              solution_zh: "将希沃姓名修改为张三。",
              changes: [],
            },
            {
              finding_id: "finding-b",
              entity_kind: "student",
              entity_name: "李四",
              entity_number: "S-002",
              class_name: "二班",
              source_locator: "csv:3",
              source_row_number: 3,
              operation_zh: "修改希沃中的学生记录",
              issue_zh: "班级不一致",
              analysis_zh: "班级需要按第三方权威值修正。",
              solution_zh: "将希沃班级修改为二班。",
              changes: [],
            },
          ],
        },
      ],
    });
    vi.mocked(agentApi.decideGraphGates).mockResolvedValue({
      decisions: [{
        gate_id: "gate-medium",
        status: "approved",
        graph_cursor: 8,
      }],
    });
    const { client } = renderPage();

    const panel = await screen.findByRole("region", { name: "中风险批量审核" });
    expect(within(panel).getByText("中风险 · 默认全部同意")).toBeInTheDocument();
    expect(within(panel).getByRole("checkbox", { name: "拒绝李四" })).not.toBeChecked();
    await user.click(within(panel).getByRole("checkbox", { name: "拒绝李四" }));
    await user.click(
      within(panel).getByRole("button", {
        name: "按当前选择继续（同意 1，拒绝 1）",
      }),
    );

    expect(agentApi.decideGraphGates).toHaveBeenCalledWith(
      "task-graph-1",
      [{
        gate_id: "gate-medium",
        decision: "approve",
        reason: "操作人完成中风险批量复核",
        approved_finding_ids: ["finding-a"],
        rejected_finding_ids: ["finding-b"],
        graph_cursor: 8,
        membership_hash: "medium-membership-hash",
      }],
    );
    expect(await screen.findByText("已拒绝李四")).toBeInTheDocument();
    client.clear();
  });

  it("consolidates every medium-risk group into one bulk review panel", async () => {
    const user = userEvent.setup();
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 8,
      current_node: "wait_high_risk_approvals",
      business_stage: "governance_execution",
      current_action_zh: "正在等待治理操作确认",
      status: "waiting_human",
      can_terminate: true,
      termination_requested: false,
      human_gates: [
        mediumGate("gate-teacher-name", "finding-teacher-name", "教师姓名修正", "张老师", "teacher"),
        mediumGate("gate-teacher-email", "finding-teacher-email", "教师邮箱修正", "李老师", "teacher"),
      ],
    });
    vi.mocked(agentApi.decideGraphGates).mockResolvedValue({
      decisions: [
        {
          gate_id: "gate-teacher-name",
          status: "approved",
          graph_cursor: 8,
        },
        {
          gate_id: "gate-teacher-email",
          status: "approved",
          graph_cursor: 8,
        },
      ],
    });
    const { client } = renderPage();

    const panel = await screen.findByRole("region", { name: "中风险批量审核" });
    expect(within(panel).getByRole("heading", { name: "修改 2 条教师记录" })).toBeInTheDocument();
    expect(within(panel).getByText("教师姓名修正")).toBeInTheDocument();
    expect(within(panel).getByText("教师邮箱修正")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "全部同意" })).not.toBeInTheDocument();
    await user.click(
      within(panel).getByRole("button", { name: "全部同意并继续" }),
    );

    expect(agentApi.decideGraphGates).toHaveBeenCalledWith(
      "task-graph-1",
      [
        expect.objectContaining({
          gate_id: "gate-teacher-name",
          decision: "approve",
          approved_finding_ids: ["finding-teacher-name"],
          rejected_finding_ids: [],
        }),
        expect.objectContaining({
          gate_id: "gate-teacher-email",
          decision: "approve",
          approved_finding_ids: ["finding-teacher-email"],
          rejected_finding_ids: [],
        }),
      ],
    );
    expect(agentApi.decideGraphGate).not.toHaveBeenCalled();
    client.clear();
  });

  it("shows a rejected high-risk group as decided", async () => {
    const user = userEvent.setup();
    vi.mocked(agentApi.decideGraphGate).mockResolvedValue({
      gate_id: "gate-1",
      status: "rejected",
      graph_cursor: 8,
    });
    const { client } = renderPage();

    await user.click(await screen.findByRole("button", { name: "拒绝" }));

    expect(agentApi.decideGraphGate).toHaveBeenCalledWith(
      "task-graph-1",
      "gate-1",
      "reject",
      "操作人拒绝高风险治理操作",
      {
        approved_finding_ids: [],
        rejected_finding_ids: ["finding-1"],
        graph_cursor: 8,
        membership_hash: "a".repeat(64),
      },
    );
    expect(await screen.findByText("已拒绝")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "同意" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拒绝" })).not.toBeInTheDocument();
    client.clear();
  });

  it("keeps a persisted approval visible after the page is reopened", async () => {
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 9,
      current_node: "compile_execution_plan",
      business_stage: "governance_execution",
      current_action_zh: "正在编译治理执行计划",
      sub_agent_zh: "治理执行 Agent",
      status: "running",
      can_terminate: true,
      termination_requested: false,
      human_gates: [
        {
          id: "gate-1",
          kind: "high_risk_approval",
          status: "approved",
          item_count: 50,
          entity_kind: "student",
          operation: "update",
          issue_kind: "field_difference",
          summary_zh: "修改 50 条学生手机号",
          risk_reason_zh: "学生手机号属于高危隐私字段，本次操作会修改希沃目标中的手机号。",
        },
      ],
    });
    const { client } = renderPage();

    expect(await screen.findByText("已允许")).toBeInTheDocument();
    expect(screen.getByText("修改 50 条学生手机号")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "同意" })).not.toBeInTheDocument();
    client.clear();
  });

  it("keeps independent decisions on visually distinct approval groups", async () => {
    const user = userEvent.setup();
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 8,
      current_node: "wait_high_risk_approvals",
      business_stage: "governance_execution",
      current_action_zh: "正在等待高风险操作审批",
      status: "waiting_human",
      can_terminate: true,
      termination_requested: false,
      human_gates: [
        {
          id: "gate-phone",
          kind: "high_risk_approval",
          status: "pending",
          risk: "high",
          cursor: 8,
          membership_hash: "b".repeat(64),
          item_count: 50,
          entity_kind: "student",
          operation: "update",
          issue_kind: "field_difference",
          summary_zh: "修改 50 条学生手机号",
          risk_reason_zh: "学生手机号属于高危隐私字段，本次操作会修改希沃目标中的手机号。",
          items: [
            {
              finding_id: "finding-phone",
              entity_kind: "student",
              entity_name: "李明",
              source_locator: "csv:12",
              operation_zh: "修改希沃中的学生记录",
              issue_zh: "手机号不一致",
              analysis_zh: "第三方权威手机号与希沃手机号不一致。",
              solution_zh: "将希沃手机号修改为第三方权威值。",
              changes: [],
            },
          ],
        },
        {
          id: "gate-delete",
          kind: "high_risk_approval",
          status: "pending",
          item_count: 3,
          entity_kind: "teacher",
          operation: "delete",
          issue_kind: "target_extra",
          summary_zh: "删除 3 条教师记录",
          risk_reason_zh: "删除会永久移除希沃目标中的记录，治理后只能通过回滚任务恢复。",
        },
      ],
    });
    vi.mocked(agentApi.decideGraphGate).mockResolvedValue({
      gate_id: "gate-phone",
      status: "approved",
      graph_cursor: 8,
    });
    const { client } = renderPage();

    const phoneCard = (await screen.findByRole(
      "heading",
      { name: "修改 50 条学生手机号" },
    )).closest("section");
    const deleteCard = screen.getByRole(
      "heading",
      { name: "删除 3 条教师记录" },
    ).closest("section");
    expect(phoneCard).not.toBeNull();
    expect(deleteCard).not.toBeNull();
    await user.click(within(phoneCard as HTMLElement).getByRole("button", { name: "同意" }));

    expect(await within(phoneCard as HTMLElement).findByText("已允许")).toBeInTheDocument();
    expect(
      within(deleteCard as HTMLElement).getByRole("button", { name: "同意" }),
    ).toBeInTheDocument();
    client.clear();
  });

  it("shows an approval failure inside the affected high-risk card", async () => {
    const user = userEvent.setup();
    vi.mocked(agentApi.decideGraphGate).mockRejectedValue(
      new Error("审批上下文已过期，请刷新后重试"),
    );
    const { client } = renderPage();

    const heading = await screen.findByRole("heading", { name: "修改 50 条学生手机号" });
    const card = heading.closest("section");
    expect(card).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "同意" }));

    expect(
      await within(card as HTMLElement).findByText("审批上下文已过期，请刷新后重试"),
    ).toBeInTheDocument();
    client.clear();
  });

  it("refreshes a stale high-risk list and explains that it must be reviewed again", async () => {
    const user = userEvent.setup();
    vi.mocked(agentApi.decideGraphGate).mockRejectedValue(
      new Error("Gate cursor is stale"),
    );
    const { client } = renderPage();

    const heading = await screen.findByRole("heading", { name: "修改 50 条学生手机号" });
    const card = heading.closest("section");
    expect(card).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "同意" }));

    expect(
      await within(card as HTMLElement).findByText(
        "审批清单已更新，请查看刷新后的操作后重新确认",
      ),
    ).toBeInTheDocument();
    expect(agentApi.graph).toHaveBeenCalledTimes(2);
    client.clear();
  });

  it("does not offer decisions for a stale approval gate", async () => {
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 14,
      current_node: "analyze_actionable_batches",
      business_stage: "agent_analysis",
      current_action_zh: "正在生成 AI 分析与治理方案",
      status: "failed",
      can_terminate: false,
      termination_requested: false,
      human_gates: [
        {
          id: "gate-stale",
          kind: "high_risk_approval",
          status: "pending",
          item_count: 1,
          entity_kind: "teacher",
          operation: "delete",
          issue_kind: "target_extra",
          summary_zh: "删除 1 条教师记录",
          risk_reason_zh: "删除会永久移除希沃目标中的记录。",
          actionable: false,
          unavailable_reason_zh: "任务已经结束或暂停，不能继续审批。",
          items: [],
        },
      ],
    });
    const { client } = renderPage();

    expect(await screen.findByText("审批不可用")).toBeInTheDocument();
    expect(
      screen.getByText("任务已经结束或暂停，不能继续审批。"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "同意" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拒绝" })).not.toBeInTheDocument();
    client.clear();
  });

  it("requires an explicit modal confirmation before terminating the graph task", async () => {
    const user = userEvent.setup();
    const preview = vi.spyOn(agentApi, "previewTermination").mockResolvedValue({
      id: "termination-gate-1",
      kind: "termination_confirmation",
      status: "pending",
      item_count: 1,
    });
    const decide = vi.spyOn(agentApi, "decideGraphGate").mockResolvedValue({
      gate_id: "termination-gate-1",
      status: "approved",
      graph_cursor: 8,
    });
    const directTerminate = vi.spyOn(agentApi, "terminate");
    const { client } = renderPage();

    await user.click(await screen.findByRole("button", { name: "终止任务" }));

    expect(preview).toHaveBeenCalledWith("task-graph-1");
    expect(directTerminate).not.toHaveBeenCalled();
    expect(
      await screen.findByRole("dialog", { name: "确认终止当前任务？" }),
    ).toBeInTheDocument();
    expect(document.querySelector(".apple-agent-modal")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认终止" }));

    expect(decide).toHaveBeenCalledWith(
      "task-graph-1",
      "termination-gate-1",
      "approve",
      "操作人确认终止当前任务",
    );
    client.clear();
  });

  it("explains that a terminated task is only generating its final report", async () => {
    vi.mocked(agentApi.task).mockResolvedValue({
      id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      task_kind: "sync",
      phase: "generate_report",
      status: "running",
      title: "全校学生数据同步",
      report_id: null,
    });
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 24,
      current_node: "termination_report",
      business_stage: "report_and_rollback",
      current_action_zh: "正在生成终止报告",
      status: "running",
      can_terminate: true,
      termination_requested: true,
      human_gates: [],
    });
    const { client } = renderPage();

    expect(
      await screen.findByRole("heading", { name: "任务已终止" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/仍在为你生成终止报告/)).toBeInTheDocument();
    expect(screen.getByText(/未开始的操作不会继续/)).toBeInTheDocument();
    expect(screen.getByText("生成终止报告")).toBeInTheDocument();
    client.clear();
  });

  it("shows a completed termination report above the event history", async () => {
    vi.mocked(agentApi.task).mockResolvedValue({
      id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      task_kind: "sync",
      phase: "terminal",
      status: "terminated",
      title: "全校学生数据同步",
      report_id: "report-1",
      rollback_eligible: true,
    });
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 25,
      current_node: "terminal",
      business_stage: "terminal",
      current_action_zh: "任务已结束",
      status: "terminated",
      can_terminate: false,
      termination_requested: true,
      human_gates: [],
    });
    vi.mocked(agentApi.events).mockResolvedValue({
      cursor: "25",
      events: [
        {
          id: "event-terminal",
          cursor: "25",
          type: "graph.transitioned",
          phase: "terminal",
          payload: { node: "terminal" },
          created_at: "2026-07-26T02:56:19Z",
        },
      ],
    });
    const { client, container } = renderPage();

    const reportHeading = await screen.findByRole(
      "heading",
      { name: "终止报告已生成" },
    );
    await screen.findByLabelText("Agent 事件");
    const reportCard = reportHeading.closest(".agent-report-summary-card");
    const eventHistory = container.querySelector(".agent-event-history");
    expect(reportCard).not.toBeNull();
    expect(eventHistory).not.toBeNull();
    expect(
      (reportCard as HTMLElement).compareDocumentPosition(
        eventHistory as HTMLElement,
      ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      within(reportCard as HTMLElement).getByRole(
        "button",
        { name: "查看任务报告" },
      ),
    ).toBeInTheDocument();
    expect(
      within(eventHistory as HTMLElement).queryByRole(
        "button",
        { name: "查看任务报告" },
      ),
    ).not.toBeInTheDocument();
    client.clear();
  });

  it("uses temporary dialogue and second confirmation for identity conflicts", async () => {
    const user = userEvent.setup();
    vi.mocked(agentApi.graph).mockResolvedValue({
      task_id: "task-graph-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 6,
      current_node: "resolve_identity_conflicts",
      business_stage: "agent_analysis",
      current_action_zh: "正在等待身份冲突说明",
      status: "waiting_human",
      can_terminate: true,
      termination_requested: false,
      human_gates: [
        {
          id: "identity-gate-1",
          kind: "identity_conflict",
          status: "pending",
          item_count: 1,
          cursor: 5,
          actionable: true,
          conflicts: [{
            clarification_id: "clarification-1",
            status: "pending",
            summary_zh: "唯一身份字段命中了多个第三方权威候选，Agent 无法安全选择。",
            subject: {
              entity_kind: "student",
              name: "测试学生",
              number: "S-009",
              class_name: "一年级一班",
              phone_masked: "***0009",
              email: "student@example.test",
            },
            candidates: [{
              entity_kind: "student",
              name: "测试学生",
              number: "S-001",
              class_name: "一年级一班",
              phone_masked: "138****0001",
              email: "student@example.test",
            }],
            allowed_outcomes: ["use_candidate", "target_extra"],
            interpretation_zh: null,
          }],
        },
      ],
    });
    const clarify = vi.spyOn(agentApi, "clarify").mockResolvedValue({
      decision_id: "decision-1",
      status: "interpreted",
      task_id: "task-graph-1",
      decision: "select_candidate",
      selected_candidate_id: "candidate-1",
      interpretation_zh: "我理解为保留编号 S-001 的候选，确认后继续。",
      requires_second_confirmation: true,
    });
    const confirm = vi.spyOn(agentApi, "confirmClarification").mockResolvedValue({
      status: "confirmed",
    });
    const { client } = renderPage();

    expect(await screen.findByRole("heading", { name: "需要人工判断身份冲突" })).toBeInTheDocument();
    expect(screen.getByText("第 1/1 条")).toBeInTheDocument();
    expect(screen.getByText("希沃记录")).toBeInTheDocument();
    expect(screen.getByText("第三方候选 A")).toBeInTheDocument();
    expect(screen.getByText("S-009")).toBeInTheDocument();
    expect(screen.getByText("S-001")).toBeInTheDocument();
    expect(screen.getByText("***0009")).toBeInTheDocument();
    expect(screen.getByText("138****0001")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "同意" })).not.toBeInTheDocument();
    await user.type(
      screen.getByRole("textbox", { name: "身份冲突处理说明" }),
      "两条记录属于同一名学生，请保留编号 S-001。",
    );
    await user.click(screen.getByRole("button", { name: "提交说明" }));

    expect(clarify).toHaveBeenCalledWith(
      "task-graph-1",
      "两条记录属于同一名学生，请保留编号 S-001。",
    );
    expect(
      await screen.findByText("我理解为保留编号 S-001 的候选，确认后继续。"),
    ).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "确认模型解释" }));
    expect(confirm).toHaveBeenCalledWith("task-graph-1", "decision-1");
    client.clear();
  });
});

function mediumGate(
  id: string,
  findingId: string,
  summary: string,
  entityName: string,
  entityKind: "student" | "teacher" | "department" = "student",
): AgentGraphHumanGate {
  return {
    id,
    kind: "high_risk_approval" as const,
    status: "pending" as const,
    risk: "medium" as const,
    cursor: 8,
    membership_hash: `${id}-membership-hash`,
    member_decisions: {},
    item_count: 1,
    entity_kind: entityKind,
    operation: "update",
    issue_kind: "field_difference",
    summary_zh: summary,
    risk_reason_zh: "该操作属于中风险变更。",
    actionable: true,
    items: [
      {
        finding_id: findingId,
        entity_kind: entityKind,
        entity_name: entityName,
        entity_number: "S-001",
        class_name: "一班",
        source_locator: "csv:2",
        source_row_number: 2,
        operation_zh: "修改希沃记录",
        issue_zh: summary,
        analysis_zh: "按第三方权威值修正。",
        solution_zh: "更新目标字段。",
        changes: [],
      },
    ],
  };
}

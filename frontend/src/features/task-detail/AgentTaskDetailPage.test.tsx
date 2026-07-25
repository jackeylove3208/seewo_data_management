import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { agentApi } from "../../api/agent";
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
      human_gates: [
        {
          id: "gate-1",
          kind: "high_risk_approval",
          status: "pending",
          item_count: 50,
          entity_kind: "student",
          operation: "update",
          issue_kind: "field_difference",
          summary_zh: "修改 50 条学生手机号",
          risk_reason_zh: "学生手机号属于高危隐私字段，本次操作会修改希沃目标中的手机号。",
        },
      ],
    });
    vi.spyOn(agentApi, "decideGraphGate").mockResolvedValue({
      gate_id: "gate-1",
      status: "approved",
      graph_cursor: 8,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders business progress without internal English event identifiers", async () => {
    const { client } = renderPage();

    expect(await screen.findAllByText("正在等待高风险操作审批")).toHaveLength(2);
    expect(screen.getByText(/离开页面不会中断任务/)).toBeInTheDocument();
    expect(screen.getByText("治理执行 Agent · 3 / 5")).toBeInTheDocument();
    expect(screen.getByText(/共 50 条记录/)).toBeInTheDocument();
    expect(screen.getByText("修改 50 条学生手机号")).toBeInTheDocument();
    expect(
      screen.getByText("学生手机号属于高危隐私字段，本次操作会修改希沃目标中的手机号。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("wait_high_risk_approvals")).not.toBeInTheDocument();
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
    );
    expect(await screen.findByText("已允许")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "同意" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拒绝" })).not.toBeInTheDocument();
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
      human_gates: [
        {
          id: "gate-phone",
          kind: "high_risk_approval",
          status: "pending",
          item_count: 50,
          entity_kind: "student",
          operation: "update",
          issue_kind: "field_difference",
          summary_zh: "修改 50 条学生手机号",
          risk_reason_zh: "学生手机号属于高危隐私字段，本次操作会修改希沃目标中的手机号。",
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
      human_gates: [
        {
          id: "identity-gate-1",
          kind: "identity_conflict",
          status: "pending",
          item_count: 2,
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

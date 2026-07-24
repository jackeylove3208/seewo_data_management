import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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
    expect(screen.queryByText("wait_high_risk_approvals")).not.toBeInTheDocument();
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

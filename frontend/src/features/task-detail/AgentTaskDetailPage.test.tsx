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
});

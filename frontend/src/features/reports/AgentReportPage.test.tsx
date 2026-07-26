import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { agentApi } from "../../api/agent";
import { AgentReportPage } from "./AgentReportPage";

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/tasks/task-report-1/report"]}>
        <Routes>
          <Route path="/tasks/:taskId/report" element={<AgentReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...rendered, client };
}

describe("Agent synchronization report", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the model narrative and local writeback result in the dark workbench", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-1",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: true,
      deletion_eligible: false,
      created_at: "2026-07-26T07:00:00Z",
      content: {
        narrative: {
          title_zh: "全校组织数据同步分析报告",
          summary_zh:
            "Agent 已完成数据核验与治理。发现两项需要处理的问题，获批操作均已写入希沃本地数据。",
        },
      },
      facts: {
        findings: [
          {
            id: "finding-1",
            category_zh: "手机号不一致",
            entity_name: "李明",
            analysis_zh: "第三方权威手机号与希沃记录不一致。",
            solution_zh: "已按审核结果更新希沃手机号。",
            operator_decision: "approved",
          },
        ],
        excluded_findings: [],
        mutations: [
          {
            id: "operation-1",
            operation: "update",
            entity_kind: "student",
            status: "succeeded",
          },
        ],
        mutation_summary: { succeeded: 1, failed: 0 },
        publication: {
          status: "published",
          source_ref: "seewo/current.csv",
        },
      },
    });

    const { client, container } = renderPage();

    expect(
      await screen.findByRole("heading", {
        name: "全校组织数据同步分析报告",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/发现两项需要处理的问题，获批操作均已写入希沃本地数据/),
    ).toBeInTheDocument();
    expect(screen.getByText("第三方权威手机号与希沃记录不一致。")).toBeInTheDocument();
    expect(screen.getByText("已按审核结果更新希沃手机号。")).toBeInTheDocument();
    expect(screen.getByText("已写回本地 CSV")).toBeInTheDocument();
    expect(screen.getByText("seewo/current.csv")).toBeInTheDocument();
    expect(container.querySelector(".agent-report-page.apple-page")).not.toBeNull();
    client.clear();
  });
});

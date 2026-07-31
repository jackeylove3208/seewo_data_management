import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
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

  it("renders the model narrative and local writeback result in the light document workbench", async () => {
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
            execution_status: "succeeded",
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
    const finding = container.querySelector(".agent-report-findings > li");
    expect(finding).not.toBeNull();
    expect(within(finding as HTMLElement).getByText("已同意")).toBeInTheDocument();
    expect(within(finding as HTMLElement).getByText("执行成功")).toBeInTheDocument();
    client.clear();
  });

  it("shows approved failures, input anomalies, and partial completion prominently", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-partial",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-07-29T08:00:00Z",
      content: {
        narrative: {
          title_zh: "部分执行同步报告",
          summary_zh: "部分获批变更未能完成。",
          input_exception_analyses: [{
            reason_code: "authority_field_unavailable",
            title_zh: "权威学生数据缺少班级字段",
            analysis_zh: "钉钉权威数据中有 7 条学生记录未提供班级字段。",
            impact_zh: "身份匹配仍可继续，但无法分析或治理学生班级差异。",
            suggestion_zh: "请检查钉钉接口权限、数据范围和班级字段映射。",
          }],
        },
      },
      facts: {
        findings: [{
          id: "finding-failed",
          category_zh: "多余教师",
          entity_name: "测试教师",
          operator_decision: "approved",
          execution_status: "failed",
        }],
        excluded_findings: [{
          reason: "目标记录缺少身份字段",
          disposition: "target_extra",
        }],
        mutations: [{
          id: "operation-failed",
          operation: "delete",
          entity_kind: "teacher",
          status: "failed",
        }],
        mutation_summary: {
          succeeded: 0,
          failed: 1,
          verification_failed: 0,
        },
        publication: {
          status: "no_changes",
          source_ref: "seewo/current.csv",
        },
      },
    });

    const { client, container } = renderPage();

    expect(await screen.findByText("部分完成")).toBeInTheDocument();
    const finding = container.querySelector(".agent-report-findings > li");
    expect(finding).not.toBeNull();
    expect(within(finding as HTMLElement).getByText("已同意")).toBeInTheDocument();
    expect(within(finding as HTMLElement).getByText("执行失败")).toBeInTheDocument();
    const exclusion = container.querySelector(".agent-report-exclusions > li");
    expect(exclusion).not.toBeNull();
    expect(
      within(exclusion as HTMLElement).getByText("输入异常"),
    ).toBeInTheDocument();
    expect(screen.getByText("权威学生数据缺少班级字段")).toBeInTheDocument();
    expect(
      screen.getByText("钉钉权威数据中有 7 条学生记录未提供班级字段。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("身份匹配仍可继续，但无法分析或治理学生班级差异。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("请检查钉钉接口权限、数据范围和班级字段映射。"),
    ).toBeInTheDocument();
    expect(container.querySelector(".agent-report-metric-error")).not.toBeNull();
    client.clear();
  });

  it("shows a no-write rollback as already restored instead of skipped", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-rollback-restored",
      task_id: "task-report-1",
      kind: "rollback",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: true,
      created_at: "2026-07-29T02:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [],
        mutations: [{
          id: "rollback-operation-1",
          operation: "update",
          entity_kind: "student",
          status: "already_restored",
        }],
        mutation_summary: {
          succeeded: 0,
          already_restored: 1,
          conflict_skipped: 0,
          failed: 0,
        },
        publication: {
          status: "no_changes",
          source_ref: "seewo/current.csv",
        },
      },
    });

    const { client } = renderPage();

    expect(await screen.findByText("回滚完成")).toBeInTheDocument();
    expect(screen.getByText("已处于原状态")).toBeInTheDocument();
    expect(screen.getByText("无需再次写入本地 CSV")).toBeInTheDocument();
    expect(screen.getByText("1", { selector: "strong" })).toBeInTheDocument();
    client.clear();
  });

  it("surfaces rollback conflicts instead of reporting successful completion", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-rollback-conflict",
      task_id: "task-report-1",
      kind: "rollback",
      terminal_state: "completed_with_conflicts",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-07-29T02:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [],
        mutations: [{
          id: "rollback-operation-1",
          operation: "update",
          entity_kind: "student",
          status: "conflict_skipped",
        }],
        mutation_summary: {
          succeeded: 0,
          already_restored: 0,
          conflict_skipped: 1,
          failed: 0,
        },
        publication: {
          status: "no_changes",
          source_ref: "seewo/current.csv",
        },
      },
    });

    const { client } = renderPage();

    expect(await screen.findByText("回滚存在冲突")).toBeInTheDocument();
    expect(screen.getByText("因当前值冲突而跳过")).toBeInTheDocument();
    expect(screen.queryByText("回滚完成")).not.toBeInTheDocument();
    client.clear();
  });
});

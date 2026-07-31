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
            reason_code: "authority_identity_absent",
            title_zh: "权威部门数据缺少身份标识",
            analysis_zh: "钉钉权威数据中有 4 条部门记录缺少身份标识。",
            impact_zh: "这些部门记录无法可靠匹配，已从治理范围排除。",
            suggestion_zh: "请补充可用于匹配的部门编号后重新运行任务。",
          }],
        },
      },
      facts: {
        findings: [
          {
            id: "finding-failed",
            kind: "target_extra",
            category_zh: "多余教师",
            entity_name: "测试教师",
            operator_decision: "approved",
            execution_status: "failed",
          },
          {
            id: "authority-invalid-1",
            kind: "authority_invalid",
            category_zh: "权威记录缺少身份标识",
            analysis_zh: "部门记录缺少编号、电话和邮箱。",
            execution_status: "not_executed",
          },
          {
            id: "authority-invalid-2",
            kind: "authority_invalid",
            category_zh: "权威记录缺少身份标识",
            analysis_zh: "部门记录缺少编号、电话和邮箱。",
            execution_status: "not_executed",
          },
        ],
        excluded_findings: [
          {
            reason: "authority_field_unavailable",
            disposition: "source_field_unavailable",
          },
          {
            reason: "authority_identity_absent",
            disposition: "mandatory_ai_anomaly",
          },
          {
            reason: "目标记录缺少身份字段",
            disposition: "target_extra",
          },
        ],
        input_diagnostics: {
          marked_input_counts: { authoritative: 4, target: 0 },
          unique_marked_input_count: 4,
          reason_counts: { authority_identity_absent: 4 },
          overlapped_reason_counts: { authority_field_unavailable: 4 },
          unavailable_field_counts: {},
          identity_absent_count: 4,
        },
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
    expect(container.querySelectorAll(".agent-report-findings > li")).toHaveLength(1);
    expect(screen.queryByText("权威记录缺少身份标识")).not.toBeInTheDocument();
    expect(within(finding as HTMLElement).getByText("已同意")).toBeInTheDocument();
    expect(within(finding as HTMLElement).getByText("执行失败")).toBeInTheDocument();
    const exclusion = container.querySelector(".agent-report-exclusions > li");
    expect(exclusion).not.toBeNull();
    expect(
      within(exclusion as HTMLElement).getByText("输入异常"),
    ).toBeInTheDocument();
    expect(screen.getByText("权威部门数据缺少身份标识")).toBeInTheDocument();
    expect(
      screen.getByText("钉钉权威数据中有 4 条部门记录缺少身份标识。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("这些部门记录无法可靠匹配，已从治理范围排除。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("请补充可用于匹配的部门编号后重新运行任务。"),
    ).toBeInTheDocument();
    const exceptionMetric = [...container.querySelectorAll(".agent-report-metrics article")]
      .find((item) => within(item as HTMLElement).queryByText("输入异常"));
    expect(within(exceptionMetric as HTMLElement).getByText("4")).toBeInTheDocument();
    expect(screen.queryByText("authority_field_unavailable")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".agent-report-exclusions > li")).toHaveLength(1);
    expect(
      container.querySelector(".agent-report-exception-analyses > li"),
    ).toHaveClass("agent-report-exception-analysis");
    expect(container.querySelector(".agent-report-metric-error")).not.toBeNull();
    client.clear();
  });

  it("renders a duplicated unexecuted finding only once", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-unexecuted",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-07-31T08:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [
          {
            id: "finding-unexecuted",
            category_zh: "待处理学生信息",
            analysis_zh: "缺少可执行的审批结论。",
            solution_zh: "等待人工审核后再执行。",
            execution_status: "not_executed",
          },
          {
            id: "finding-unexecuted",
            category_zh: "待处理学生信息",
            analysis_zh: "缺少可执行的审批结论。",
            solution_zh: "等待人工审核后再执行。",
            execution_status: "not_executed",
          },
        ],
        excluded_findings: [],
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client, container } = renderPage();

    await screen.findByText("待处理学生信息");
    expect(container.querySelectorAll(".agent-report-findings > li")).toHaveLength(1);
    expect(screen.getAllByText("缺少可执行的审批结论。")).toHaveLength(1);
    expect(screen.getByText("未执行")).toBeInTheDocument();
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

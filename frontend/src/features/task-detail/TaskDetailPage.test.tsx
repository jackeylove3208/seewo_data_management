import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type PropsWithChildren } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { agentApi } from "../../api/agent";
import { ingestionApi } from "../../api/ingestion";
import { reconciliationApi } from "../../api/reconciliation";
import { saveStoredTask } from "../../data/taskHistory";
import { TaskDetailPage } from "./TaskDetailPage";

function wrapper({ children }: PropsWithChildren) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}><MemoryRouter initialEntries={["/tasks/real-1"]}>{children}</MemoryRouter></QueryClientProvider>;
}

describe("real task detail", () => {
  beforeEach(() => {
    localStorage.clear();
    saveStoredTask({
      id: "real-1",
      title: "教师数据核对",
      createdAt: "2026-07-17T10:00:00Z",
      sourceFile: "third_party.csv",
      targetFile: "seewo.csv",
      sourceAccepted: 10,
      targetAccepted: 10,
      issueCount: 0,
      status: "processing",
      selectedEntityTypes: ["teacher"],
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("identifies an unknown graph task before requesting the legacy workflow", async () => {
    localStorage.clear();
    const legacyTask = vi.spyOn(ingestionApi, "getTask");
    vi.spyOn(agentApi, "task").mockResolvedValue({
      id: "real-1",
      workflow_version: "agent-graph-v1",
      task_kind: "sync",
      phase: "aggregate_risk_and_approvals",
      status: "waiting_human",
      title: "全校学生数据同步",
    });
    vi.spyOn(agentApi, "events").mockResolvedValue({ cursor: "0", events: [] });
    vi.spyOn(agentApi, "graph").mockResolvedValue({
      task_id: "real-1",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 8,
      current_node: "wait_high_risk_approvals",
      business_stage: "governance_execution",
      current_action_zh: "正在等待高风险操作审批",
      status: "waiting_human",
      can_terminate: true,
      human_gates: [],
    });

    render(<Routes><Route path="/tasks/:taskId" element={<TaskDetailPage />} /></Routes>, { wrapper });

    expect(await screen.findByText("全校学生数据同步")).toBeInTheDocument();
    expect(legacyTask).not.toHaveBeenCalled();
  });

  it("renders persisted AI progress and analysis activity", async () => {
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue({
      id: "real-1",
      tenant_id: "school-1",
      scope_id: "all",
      status: "ready",
      stage: "differences_ready",
      entity_types: ["teacher"],
      snapshots: {
        authoritative: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
        target: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
      },
      workflow: {
        stage: "analysis",
        status: "pending",
        attempt: 1,
        processed: 2,
        total: 5,
        analysis: { job_id: "job-1", total: 5, completed: 2, succeeded: 1, manual_review: 1, failed: 0 },
        error: null,
      },
      error: null,
    });
    vi.spyOn(reconciliationApi, "advance").mockImplementation(() => new Promise(() => undefined));
    vi.spyOn(reconciliationApi, "getAnalysisJob").mockResolvedValue({
      job_id: "job-1",
      task_id: "real-1",
      status: "running",
      total: 5,
      completed: 2,
      succeeded: 1,
      manual_required: 1,
      needs_information: 1,
      manual_only: 0,
      failed: 0,
      proposal_ready: 1,
      last_error: null,
      updated_at: "2026-07-20T10:00:00Z",
    });
    vi.spyOn(reconciliationApi, "listDifferences").mockResolvedValue({ items: [], next_cursor: null });

    render(<Routes><Route path="/tasks/:taskId" element={<TaskDetailPage />} /></Routes>, { wrapper });

    expect(await screen.findByText("AI 分析中")).toBeInTheDocument();
    expect(screen.getByText("已完成 2 / 5")).toBeInTheDocument();
    expect(await screen.findByText((_, element) => element?.tagName === "SMALL" && element.textContent?.includes("待补信息 1") === true)).toBeInTheDocument();
    expect(await screen.findByText(/最近更新/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "取消分析" })).toBeInTheDocument();
    expect(screen.queryByText("问题类型对照")).not.toBeInTheDocument();
    expect(screen.queryByText("演示差异")).not.toBeInTheDocument();
  });

  it("shows only non-empty issue types and the batch action after terminal analysis", async () => {
    saveStoredTask({
      id: "real-1",
      title: "教师数据核对",
      createdAt: "2026-07-17T10:00:00Z",
      sourceFile: "third_party.csv",
      targetFile: "seewo.csv",
      sourceAccepted: 10,
      targetAccepted: 10,
      issueCount: 2,
      status: "processing",
      selectedEntityTypes: ["teacher", "class"],
    });
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue({
      id: "real-1",
      tenant_id: "school-1",
      scope_id: "all",
      status: "ready",
      stage: "analysis_ready",
      entity_types: ["teacher", "class"],
      snapshots: {
        authoritative: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
        target: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
      },
      workflow: {
        stage: "complete",
        status: "succeeded",
        attempt: 1,
        processed: 2,
        total: 2,
        analysis: { job_id: "job-1", total: 2, completed: 2, succeeded: 1, manual_review: 1, failed: 0 },
        error: null,
      },
      error: null,
    });
    vi.spyOn(reconciliationApi, "getAnalysisJob").mockResolvedValue({
      job_id: "job-1",
      task_id: "real-1",
      status: "completed",
      total: 2,
      completed: 2,
      succeeded: 1,
      manual_required: 1,
      needs_information: 0,
      manual_only: 1,
      failed: 0,
      proposal_ready: 1,
      last_error: null,
      updated_at: "2026-07-20T10:01:00Z",
    });
    vi.spyOn(reconciliationApi, "getAnalysisSummary").mockResolvedValue({
      task_id: "real-1",
      analysis_job_id: "job-1",
      job_status: "completed",
      terminal: true,
      entity_types: [
        { entity_type: "teacher", issue_count: 2, proposal_ready: 1, needs_information: 0, manual_only: 1, failed: 0 },
        { entity_type: "class", issue_count: 0, proposal_ready: 0, needs_information: 0, manual_only: 0, failed: 0 },
      ],
    });

    render(<Routes><Route path="/tasks/:taskId" element={<TaskDetailPage />} /></Routes>, { wrapper });

    expect(await screen.findByText("问题类型对照")).toBeInTheDocument();
    expect(screen.getByText("教师")).toBeInTheDocument();
    expect(screen.queryByText("班级")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AI 一键处理" })).toBeInTheDocument();
  });

  it("keeps summaries hidden and offers resume after analysis is canceled", async () => {
    const user = userEvent.setup();
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue({
      id: "real-1",
      tenant_id: "school-1",
      scope_id: "all",
      status: "ready",
      stage: "differences_ready",
      entity_types: ["teacher"],
      snapshots: {
        authoritative: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
        target: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
      },
      workflow: {
        stage: "analysis",
        status: "running",
        attempt: 1,
        processed: 1,
        total: 5,
        analysis: { job_id: "job-1", total: 5, completed: 1, succeeded: 1, manual_review: 0, failed: 0 },
        error: null,
      },
      error: null,
    });
    vi.spyOn(reconciliationApi, "getAnalysisJob").mockResolvedValue({
      job_id: "job-1",
      task_id: "real-1",
      status: "canceled",
      total: 5,
      completed: 1,
      succeeded: 1,
      manual_required: 0,
      needs_information: 0,
      manual_only: 0,
      failed: 0,
      proposal_ready: 1,
      last_error: null,
      updated_at: "2026-07-20T10:01:00Z",
    });
    const retry = vi.spyOn(reconciliationApi, "retryAnalysisJob").mockResolvedValue({
      job_id: "job-1",
      task_id: "real-1",
      status: "running",
      total: 5,
      completed: 1,
      succeeded: 1,
      manual_required: 0,
      needs_information: 0,
      manual_only: 0,
      failed: 0,
      proposal_ready: 1,
      last_error: null,
      updated_at: "2026-07-20T10:02:00Z",
    });
    const summary = vi.spyOn(reconciliationApi, "getAnalysisSummary");

    render(<Routes><Route path="/tasks/:taskId" element={<TaskDetailPage />} /></Routes>, { wrapper });

    expect((await screen.findAllByText("分析已取消")).length).toBeGreaterThan(0);
    expect(screen.queryByText("问题类型对照")).not.toBeInTheDocument();
    expect(summary).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "继续分析" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("hides stale terminal content while failed analyses are being retried", async () => {
    const user = userEvent.setup();
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue({
      id: "real-1",
      tenant_id: "school-1",
      scope_id: "all",
      status: "ready",
      stage: "analysis_ready",
      entity_types: ["teacher"],
      snapshots: {
        authoritative: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
        target: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
      },
      workflow: {
        stage: "complete",
        status: "succeeded",
        attempt: 1,
        processed: 2,
        total: 2,
        analysis: { job_id: "job-1", total: 2, completed: 2, succeeded: 1, manual_review: 0, failed: 1 },
        error: null,
      },
      error: null,
    });
    vi.spyOn(reconciliationApi, "getAnalysisJob").mockResolvedValue({
      job_id: "job-1",
      task_id: "real-1",
      status: "completed_with_failures",
      total: 2,
      completed: 2,
      succeeded: 1,
      manual_required: 0,
      needs_information: 0,
      manual_only: 0,
      failed: 1,
      proposal_ready: 1,
      last_error: "gateway_timeout",
      updated_at: "2026-07-20T10:01:00Z",
    });
    vi.spyOn(reconciliationApi, "getAnalysisSummary").mockResolvedValue({
      task_id: "real-1",
      analysis_job_id: "job-1",
      job_status: "completed_with_failures",
      terminal: true,
      entity_types: [{ entity_type: "teacher", issue_count: 2, proposal_ready: 1, needs_information: 0, manual_only: 0, failed: 1 }],
    });
    vi.spyOn(reconciliationApi, "retryAnalysisJob").mockResolvedValue({
      job_id: "job-1",
      task_id: "real-1",
      status: "running",
      total: 2,
      completed: 1,
      succeeded: 1,
      manual_required: 0,
      needs_information: 0,
      manual_only: 0,
      failed: 0,
      proposal_ready: 1,
      last_error: null,
      updated_at: "2026-07-20T10:02:00Z",
    });

    render(<Routes><Route path="/tasks/:taskId" element={<TaskDetailPage />} /></Routes>, { wrapper });

    expect(await screen.findByText("问题类型对照")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试失败项" }));

    await waitFor(() => expect(screen.queryByText("问题类型对照")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "AI 一键处理" })).not.toBeInTheDocument();
    expect(screen.getByText("已完成 1 / 2")).toBeInTheDocument();
  });

  it("shows a recoverable Chinese error when automatic workflow advancement fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue({
      id: "real-1",
      tenant_id: "school-1",
      scope_id: "all",
      status: "ready",
      stage: "snapshots",
      entity_types: ["teacher"],
      snapshots: {
        authoritative: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
        target: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
      },
      workflow: {
        stage: "matching",
        status: "pending",
        attempt: 1,
        processed: 0,
        total: 0,
        analysis: { job_id: null, total: 0, completed: 0, succeeded: 0, manual_review: 0, failed: 0 },
        error: null,
      },
      error: null,
    });
    const advance = vi.spyOn(reconciliationApi, "advance")
      .mockRejectedValueOnce(new Error("internal connection failure"))
      .mockImplementation(() => new Promise(() => undefined));

    render(<Routes><Route path="/tasks/:taskId" element={<TaskDetailPage />} /></Routes>, { wrapper });

    expect(await screen.findByText("任务处理未继续")).toBeInTheDocument();
    expect(screen.queryByText("internal connection failure")).not.toBeInTheDocument();
    expect(advance).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "继续处理" }));
    await waitFor(() => expect(advance).toHaveBeenCalledTimes(2));
  });

  it("does not report zero while the terminal summary is unavailable", async () => {
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue({
      id: "real-1",
      tenant_id: "school-1",
      scope_id: "all",
      status: "ready",
      stage: "analysis_ready",
      entity_types: ["teacher"],
      snapshots: {
        authoritative: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
        target: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
      },
      workflow: {
        stage: "complete",
        status: "succeeded",
        attempt: 1,
        processed: 2,
        total: 2,
        analysis: { job_id: "job-1", total: 2, completed: 2, succeeded: 2, manual_review: 0, failed: 0 },
        error: null,
      },
      error: null,
    });
    vi.spyOn(reconciliationApi, "getAnalysisJob").mockResolvedValue({
      job_id: "job-1",
      task_id: "real-1",
      status: "completed",
      total: 2,
      completed: 2,
      succeeded: 2,
      manual_required: 0,
      needs_information: 0,
      manual_only: 0,
      failed: 0,
      proposal_ready: 2,
      last_error: null,
      updated_at: "2026-07-20T10:01:00Z",
    });
    vi.spyOn(reconciliationApi, "getAnalysisSummary").mockRejectedValue(new Error("database detail"));

    render(<Routes><Route path="/tasks/:taskId" element={<TaskDetailPage />} /></Routes>, { wrapper });

    expect(await screen.findByText("问题汇总读取失败")).toBeInTheDocument();
    expect(within(screen.getByText("发现问题").parentElement!).getByText("--")).toBeInTheDocument();
    expect(screen.queryByText("database detail")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试问题汇总" })).toBeInTheDocument();
  });
});

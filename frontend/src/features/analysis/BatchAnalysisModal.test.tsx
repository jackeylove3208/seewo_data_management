import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { reconciliationApi } from "../../api/reconciliation";
import { BatchAnalysisModal } from "./BatchAnalysisModal";

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Provider({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("BatchAnalysisModal", () => {
  afterEach(() => vi.restoreAllMocks());

  it("previews exclusions and creates pending-execution proposals only", async () => {
    const user = userEvent.setup();
    vi.spyOn(reconciliationApi, "previewProposalBatch").mockResolvedValue({
      task_id: "task-1",
      analysis_job_id: "job-1",
      preview_token: "signed-preview-token-value",
      included: [{
        difference_id: "difference-1",
        difference_version: 1,
        analysis_id: "analysis-1",
        solution_id: "solution-1",
        entity_type: "teacher",
        title: "更新教师手机号",
        operation_type: "update",
        changes: [{ field: "phone", before: "13900000000", after: "13800000000" }],
        risk: "low",
      }],
      excluded: [{
        difference_id: "difference-2",
        entity_type: "teacher",
        reason: "manual_only",
        reason_label: "仅支持人工处理",
      }],
    });
    const confirm = vi.spyOn(reconciliationApi, "confirmProposalBatch").mockResolvedValue({
      task_id: "task-1",
      created: 1,
      skipped: 0,
      failed: 0,
      items: [{ difference_id: "difference-1", status: "created", proposal_id: "proposal-1", reason: null }],
    });

    render(<BatchAnalysisModal open taskId="task-1" jobId="job-1" onClose={() => undefined} />, { wrapper: wrapper() });

    expect(await screen.findByText("更新教师手机号")).toBeInTheDocument();
    expect(screen.getByText(/仅支持人工处理/)).toBeInTheDocument();
    expect(screen.getByText("确认后仅生成待执行方案，不会直接修改希沃数据。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认生成 1 份待执行方案" }));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("task-1", expect.objectContaining({
      preview_token: "signed-preview-token-value",
      idempotency_key: expect.any(String),
    })));
    expect(await screen.findByText("已生成 1 份待执行方案")).toBeInTheDocument();
  });

  it("reuses the same idempotency key when confirmation is retried", async () => {
    const user = userEvent.setup();
    vi.spyOn(reconciliationApi, "previewProposalBatch").mockResolvedValue({
      task_id: "task-1",
      analysis_job_id: "job-1",
      preview_token: "signed-preview-token-value",
      included: [{
        difference_id: "difference-1",
        difference_version: 1,
        analysis_id: "analysis-1",
        solution_id: "solution-1",
        entity_type: "teacher",
        title: "更新教师手机号",
        operation_type: "update",
        changes: [{ field: "phone", before: "13900000000", after: "13800000000" }],
        risk: "low",
      }],
      excluded: [],
    });
    const confirm = vi.spyOn(reconciliationApi, "confirmProposalBatch")
      .mockRejectedValueOnce(new Error("网络连接中断"))
      .mockResolvedValueOnce({
        task_id: "task-1",
        created: 1,
        skipped: 0,
        failed: 0,
        items: [{ difference_id: "difference-1", status: "created", proposal_id: "proposal-1", reason: null }],
      });

    render(<BatchAnalysisModal open taskId="task-1" jobId="job-1" onClose={() => undefined} />, { wrapper: wrapper() });
    const button = await screen.findByRole("button", { name: "确认生成 1 份待执行方案" });
    await user.click(button);
    expect(await screen.findByText("批量确认失败")).toBeInTheDocument();
    await user.click(button);
    expect(await screen.findByText("已生成 1 份待执行方案")).toBeInTheDocument();

    const firstKey = confirm.mock.calls[0][1].idempotency_key;
    const secondKey = confirm.mock.calls[1][1].idempotency_key;
    expect(secondKey).toBe(firstKey);
  });

  it("localizes status values in batch previews", async () => {
    vi.spyOn(reconciliationApi, "previewProposalBatch").mockResolvedValue({
      task_id: "task-1",
      analysis_job_id: "job-1",
      preview_token: "signed-preview-token-value",
      included: [{
        difference_id: "difference-1",
        difference_version: 1,
        analysis_id: "analysis-1",
        solution_id: "solution-1",
        entity_type: "teacher",
        title: "停用离职教师账号",
        operation_type: "update",
        changes: [{ field: "status", before: "active", after: "inactive" }],
        risk: "low",
      }],
      excluded: [],
    });

    render(<BatchAnalysisModal open taskId="task-1" jobId="job-1" onClose={() => undefined} />, { wrapper: wrapper() });

    expect(await screen.findByText("启用")).toBeInTheDocument();
    expect(screen.getByText("停用")).toBeInTheDocument();
    expect(screen.queryByText("active")).not.toBeInTheDocument();
    expect(screen.queryByText("inactive")).not.toBeInTheDocument();
  });
});

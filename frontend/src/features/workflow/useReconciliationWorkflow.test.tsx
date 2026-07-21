import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ingestionApi, type ReconciliationTaskResponse } from "../../api/ingestion";
import { reconciliationApi, type WorkflowState } from "../../api/reconciliation";
import { useReconciliationWorkflow } from "./useReconciliationWorkflow";

const progress = { job_id: null, total: 0, completed: 0, succeeded: 0, manual_review: 0, failed: 0 };

function workflow(stage: WorkflowState["stage"], status: WorkflowState["status"] = "pending"): WorkflowState {
  return { stage, status, attempt: 0, processed: 0, total: 0, analysis: progress, error: null };
}

function task(state: WorkflowState): ReconciliationTaskResponse {
  return {
    id: "task-1",
    tenant_id: "school-1",
    scope_id: "all",
    status: "ready",
    stage: "snapshots",
    entity_types: ["teacher"],
    snapshots: {
      authoritative: { accepted: 1, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
      target: { accepted: 1, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
    },
    workflow: state,
    error: null,
  };
}

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function TestProvider({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("useReconciliationWorkflow", () => {
  afterEach(() => vi.restoreAllMocks());

  it("advances deterministic stages and stops once the durable analysis job is running", async () => {
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue(task(workflow("matching")));
    const advance = vi.spyOn(reconciliationApi, "advance")
      .mockResolvedValueOnce({ task_id: "task-1", workflow: workflow("differences") })
      .mockResolvedValueOnce({
        task_id: "task-1",
        workflow: {
          ...workflow("analysis", "running"),
          analysis: { ...progress, job_id: "job-1", total: 3 },
        },
      });

    const { result } = renderHook(() => useReconciliationWorkflow("task-1", true), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.task.data?.workflow.status).toBe("running"));
    expect(advance).toHaveBeenCalledTimes(2);
  });

  it("does not duplicate an in-flight advancement", async () => {
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue(task(workflow("matching")));
    let resolveAdvance: ((value: { task_id: string; workflow: WorkflowState }) => void) | undefined;
    const advance = vi.spyOn(reconciliationApi, "advance").mockImplementation(() => new Promise((resolve) => {
      resolveAdvance = resolve;
    }));

    const { rerender } = renderHook(() => useReconciliationWorkflow("task-1", true), { wrapper: wrapper() });
    await waitFor(() => expect(advance).toHaveBeenCalledTimes(1));
    rerender();
    expect(advance).toHaveBeenCalledTimes(1);
    resolveAdvance?.({ task_id: "task-1", workflow: workflow("complete", "succeeded") });
  });

  it("does not advance analysis again when a durable job id already exists", async () => {
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue(task({
      ...workflow("analysis"),
      analysis: { ...progress, job_id: "job-1", total: 3 },
    }));
    const advance = vi.spyOn(reconciliationApi, "advance");

    const { result } = renderHook(() => useReconciliationWorkflow("task-1", true), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.task.data?.workflow.analysis.job_id).toBe("job-1"));
    expect(advance).not.toHaveBeenCalled();
  });

  it("stops after an advance request fails until the operator continues", async () => {
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue(task(workflow("matching")));
    const advance = vi.spyOn(reconciliationApi, "advance")
      .mockRejectedValueOnce(new Error("network unavailable"))
      .mockImplementation(() => new Promise(() => undefined));

    const { result } = renderHook(() => useReconciliationWorkflow("task-1", true), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.advanceError).toEqual(new Error("network unavailable")));
    expect(advance).toHaveBeenCalledTimes(1);

    act(() => result.current.continueAdvance());
    await waitFor(() => expect(advance).toHaveBeenCalledTimes(2));
  });
});

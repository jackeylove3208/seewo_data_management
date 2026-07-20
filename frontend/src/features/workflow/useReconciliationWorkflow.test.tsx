import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ingestionApi, type ReconciliationTaskResponse } from "../../api/ingestion";
import { reconciliationApi, type WorkflowState } from "../../api/reconciliation";
import { useReconciliationWorkflow } from "./useReconciliationWorkflow";

const progress = { total: 0, completed: 0, succeeded: 0, manual_review: 0, failed: 0 };

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

  it("advances each persisted stage once until complete", async () => {
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue(task(workflow("matching")));
    const advance = vi.spyOn(reconciliationApi, "advance")
      .mockResolvedValueOnce({ task_id: "task-1", workflow: workflow("differences") })
      .mockResolvedValueOnce({ task_id: "task-1", workflow: workflow("analysis") })
      .mockResolvedValueOnce({ task_id: "task-1", workflow: workflow("complete", "succeeded") });

    const { result } = renderHook(() => useReconciliationWorkflow("task-1", true), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.task.data?.workflow.stage).toBe("complete"));
    expect(advance).toHaveBeenCalledTimes(3);
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
});

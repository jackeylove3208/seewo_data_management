import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { reconciliationApi, type AnalysisJobProgress } from "../../api/reconciliation";
import { useAnalysisJob } from "./useAnalysisJob";

const running: AnalysisJobProgress = {
  job_id: "job-1",
  task_id: "task-1",
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
  updated_at: "2026-07-20T10:00:00Z",
};

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Provider({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("useAnalysisJob", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("loads persisted progress even when EventSource is unavailable", async () => {
    vi.stubGlobal("EventSource", undefined);
    const get = vi.spyOn(reconciliationApi, "getAnalysisJob").mockResolvedValue(running);

    const { result } = renderHook(() => useAnalysisJob("job-1", true), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.job.data?.completed).toBe(1));
    expect(get).toHaveBeenCalledWith("job-1", expect.any(AbortSignal));
  });

  it("applies committed SSE progress to the query cache", async () => {
    let progressListener: ((event: MessageEvent) => void) | undefined;
    class EventSourceStub {
      addEventListener(name: string, listener: EventListener) {
        if (name === "progress") progressListener = listener as (event: MessageEvent) => void;
      }
      close() {}
    }
    vi.stubGlobal("EventSource", EventSourceStub);
    vi.spyOn(reconciliationApi, "getAnalysisJob").mockResolvedValue(running);

    const { result } = renderHook(() => useAnalysisJob("job-1", true), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.job.data?.completed).toBe(1));
    progressListener?.(new MessageEvent("progress", {
      data: JSON.stringify({ ...running, completed: 2, succeeded: 2, proposal_ready: 2 }),
    }));

    await waitFor(() => expect(result.current.job.data?.completed).toBe(2));
  });

  it("opens a new event stream when a canceled job is resumed", async () => {
    let streams = 0;
    class EventSourceStub {
      constructor() { streams += 1; }
      addEventListener() {}
      close() {}
    }
    vi.stubGlobal("EventSource", EventSourceStub);
    vi.spyOn(reconciliationApi, "getAnalysisJob").mockResolvedValue({ ...running, status: "canceled" });
    vi.spyOn(reconciliationApi, "retryAnalysisJob").mockResolvedValue(running);

    const { result } = renderHook(() => useAnalysisJob("job-1", true), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.job.data?.status).toBe("canceled"));
    expect(streams).toBe(0);
    act(() => result.current.retry());

    await waitFor(() => expect(result.current.job.data?.status).toBe("running"));
    expect(streams).toBe(1);
  });
});

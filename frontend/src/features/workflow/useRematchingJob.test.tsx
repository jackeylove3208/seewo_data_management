import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { reconciliationApi, type RematchingJobProgress } from "../../api/reconciliation";
import { useRematchingJob } from "./useRematchingJob";

const running: RematchingJobProgress = {
  job_id: "rematch-1",
  task_id: "task-1",
  status: "running",
  initial_unresolved: 12,
  indexed: 10,
  processed: 4,
  ai_recovered: 2,
  no_match: 1,
  manual_review: 1,
  conflict: 0,
  failed: 0,
  updated_at: "2026-07-20T10:00:00Z",
};

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Provider({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("useRematchingJob", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("restores persisted progress by job id when the page reloads", async () => {
    vi.stubGlobal("EventSource", undefined);
    const get = vi.spyOn(reconciliationApi, "getRematchingJob").mockResolvedValue(running);

    const { result } = renderHook(() => useRematchingJob("rematch-1", true), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.job.data?.processed).toBe(4));
    expect(get).toHaveBeenCalledWith("rematch-1", expect.any(AbortSignal));
  });

  it("polls every two seconds while a job is non-terminal", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", undefined);
    const get = vi.spyOn(reconciliationApi, "getRematchingJob").mockResolvedValue(running);

    renderHook(() => useRematchingJob("rematch-1", true), { wrapper: wrapper() });
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(get).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("applies SSE progress and closes the stream at a terminal status", async () => {
    let progressListener: ((event: MessageEvent) => void) | undefined;
    const close = vi.fn();
    class EventSourceStub {
      addEventListener(name: string, listener: EventListener) {
        if (name === "progress") progressListener = listener as (event: MessageEvent) => void;
      }
      close = close;
    }
    vi.stubGlobal("EventSource", EventSourceStub);
    vi.spyOn(reconciliationApi, "getRematchingJob").mockResolvedValue(running);

    const { result } = renderHook(() => useRematchingJob("rematch-1", true), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.job.data?.processed).toBe(4));
    progressListener?.(new MessageEvent("progress", {
      data: JSON.stringify({ ...running, status: "completed", processed: 10, ai_recovered: 8 }),
    }));

    await waitFor(() => expect(result.current.job.data?.status).toBe("completed"));
    expect(close).toHaveBeenCalled();
  });

  it("retries and cancels through durable job commands", async () => {
    vi.stubGlobal("EventSource", undefined);
    vi.spyOn(reconciliationApi, "getRematchingJob").mockResolvedValue(running);
    const retry = vi.spyOn(reconciliationApi, "retryRematchingJob").mockResolvedValue(running);
    const cancel = vi.spyOn(reconciliationApi, "cancelRematchingJob").mockResolvedValue({ ...running, status: "canceled" });

    const { result } = renderHook(() => useRematchingJob("rematch-1", true), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.job.data).toBeDefined());
    act(() => result.current.retry());
    await waitFor(() => expect(retry).toHaveBeenCalledWith("rematch-1"));
    act(() => result.current.cancel());
    await waitFor(() => expect(cancel).toHaveBeenCalledWith("rematch-1"));
    await waitFor(() => expect(result.current.job.data?.status).toBe("canceled"));
  });
});

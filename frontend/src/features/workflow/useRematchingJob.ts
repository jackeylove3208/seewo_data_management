import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { resolveApiUrl } from "../../api/client";
import { queryKeys } from "../../api/queryKeys";
import { reconciliationApi, type RematchingJobProgress } from "../../api/reconciliation";

const terminalStatuses = new Set(["completed", "completed_with_failures", "canceled"]);

export function useRematchingJob(jobId: string | null | undefined, enabled: boolean) {
  const queryClient = useQueryClient();
  const job = useQuery({
    queryKey: queryKeys.rematchingJob(jobId ?? ""),
    queryFn: ({ signal }) => reconciliationApi.getRematchingJob(jobId!, signal),
    enabled: enabled && Boolean(jobId),
    staleTime: 0,
    refetchInterval: (query) => terminalStatuses.has(query.state.data?.status ?? "") ? false : 2_000,
  });
  const retry = useMutation({
    mutationFn: () => reconciliationApi.retryRematchingJob(jobId!),
    onSuccess: (progress) => {
      queryClient.setQueryData(queryKeys.rematchingJob(progress.job_id), progress);
      queryClient.removeQueries({ queryKey: queryKeys.matchingQuality(progress.task_id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.task(progress.task_id) });
    },
  });
  const cancel = useMutation({
    mutationFn: () => reconciliationApi.cancelRematchingJob(jobId!),
    onSuccess: (progress) => queryClient.setQueryData(queryKeys.rematchingJob(progress.job_id), progress),
  });

  useEffect(() => {
    const status = job.data?.status;
    if (!enabled || !jobId || !status || terminalStatuses.has(status) || typeof EventSource === "undefined") return;
    const source = new EventSource(resolveApiUrl(`/api/entity-rematch-jobs/${jobId}/events`));
    const onProgress = (event: Event) => {
      if (!(event instanceof MessageEvent)) return;
      try {
        const progress = JSON.parse(event.data) as RematchingJobProgress;
        queryClient.setQueryData(queryKeys.rematchingJob(jobId), progress);
        if (terminalStatuses.has(progress.status)) {
          source.close();
          void queryClient.invalidateQueries({ queryKey: queryKeys.task(progress.task_id) });
          void queryClient.invalidateQueries({ queryKey: queryKeys.matchingQuality(progress.task_id) });
        }
      } catch {
        // Two-second polling remains active when an event payload cannot be parsed.
      }
    };
    source.addEventListener("progress", onProgress);
    source.onerror = () => {
      // Native EventSource reconnects; polling remains the bounded fallback.
    };
    return () => source.close();
  }, [enabled, job.data?.status, jobId, queryClient]);

  return {
    job,
    retry: () => retry.mutate(),
    retrying: retry.isPending,
    retryError: retry.error,
    cancel: () => cancel.mutate(),
    canceling: cancel.isPending,
    cancelError: cancel.error,
  };
}

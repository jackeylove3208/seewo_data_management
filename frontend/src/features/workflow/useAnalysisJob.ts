import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { resolveApiUrl } from "../../api/client";
import { queryKeys } from "../../api/queryKeys";
import { reconciliationApi, type AnalysisJobProgress } from "../../api/reconciliation";

const terminalStatuses = new Set(["completed", "completed_with_failures", "canceled"]);

export function useAnalysisJob(jobId: string | null | undefined, enabled: boolean) {
  const queryClient = useQueryClient();
  const job = useQuery({
    queryKey: queryKeys.analysisJob(jobId ?? ""),
    queryFn: ({ signal }) => reconciliationApi.getAnalysisJob(jobId!, signal),
    enabled: enabled && Boolean(jobId),
    refetchInterval: (query) => terminalStatuses.has(query.state.data?.status ?? "") ? false : 2_000,
  });
  const retry = useMutation({
    mutationFn: () => reconciliationApi.retryAnalysisJob(jobId!),
    onSuccess: (progress) => {
      queryClient.setQueryData(queryKeys.analysisJob(progress.job_id), progress);
      queryClient.removeQueries({ queryKey: queryKeys.analysisSummary(progress.task_id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.task(progress.task_id) });
    },
  });
  const cancel = useMutation({
    mutationFn: () => reconciliationApi.cancelAnalysisJob(jobId!),
    onSuccess: (progress) => queryClient.setQueryData(queryKeys.analysisJob(progress.job_id), progress),
  });

  useEffect(() => {
    const status = job.data?.status;
    if (!enabled || !jobId || !status || terminalStatuses.has(status) || typeof EventSource === "undefined") return;
    const source = new EventSource(resolveApiUrl(`/api/analysis-jobs/${jobId}/events`));
    const onProgress = (event: Event) => {
      if (!(event instanceof MessageEvent)) return;
      try {
        const progress = JSON.parse(event.data) as AnalysisJobProgress;
        queryClient.setQueryData(queryKeys.analysisJob(jobId), progress);
        if (terminalStatuses.has(progress.status)) {
          source.close();
          void queryClient.invalidateQueries({ queryKey: queryKeys.task(progress.task_id) });
          void queryClient.invalidateQueries({ queryKey: queryKeys.analysisSummary(progress.task_id) });
        }
      } catch {
        // Polling remains active when an intermediary sends an invalid event.
      }
    };
    source.addEventListener("progress", onProgress);
    source.onerror = () => {
      // Native EventSource reconnects with Last-Event-ID; polling remains the fallback.
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

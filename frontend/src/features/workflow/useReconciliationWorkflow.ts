import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { ingestionApi, type ReconciliationTaskResponse } from "../../api/ingestion";
import { queryKeys } from "../../api/queryKeys";
import { reconciliationApi, type WorkflowState } from "../../api/reconciliation";

function stageSignature(workflow: WorkflowState) {
  return `${workflow.stage}:${workflow.attempt}:${workflow.analysis.completed}`;
}

export function useReconciliationWorkflow(taskId: string, enabled: boolean) {
  const queryClient = useQueryClient();
  const attemptedStages = useRef(new Set<string>());
  const task = useQuery({
    queryKey: queryKeys.task(taskId),
    queryFn: ({ signal }) => ingestionApi.getTask(taskId, signal),
    enabled: enabled && Boolean(taskId),
    staleTime: 0,
  });

  function updateWorkflow(workflow: WorkflowState) {
    queryClient.setQueryData<ReconciliationTaskResponse>(
      queryKeys.task(taskId),
      (current) => current ? { ...current, workflow } : current,
    );
  }

  const advance = useMutation({
    mutationFn: () => reconciliationApi.advance(taskId),
    onSuccess: ({ workflow }) => updateWorkflow(workflow),
  });
  const retry = useMutation({
    mutationFn: () => reconciliationApi.retry(taskId),
    onSuccess: ({ workflow }) => {
      attemptedStages.current.clear();
      updateWorkflow(workflow);
    },
  });

  const workflow = task.data?.workflow;
  useEffect(() => {
    if (!enabled || !workflow || advance.isPending || retry.isPending) return;
    if (workflow.stage === "complete" || workflow.status === "failed") return;
    if (workflow.status !== "pending" && workflow.status !== "succeeded") return;
    const signature = stageSignature(workflow);
    if (attemptedStages.current.has(signature)) return;
    attemptedStages.current.add(signature);
    advance.mutate();
  }, [advance, enabled, retry.isPending, workflow]);

  return {
    task,
    advancing: advance.isPending,
    advanceError: advance.error,
    retrying: retry.isPending,
    retryError: retry.error,
    canRetry: Boolean(workflow?.status === "failed" && workflow.error?.retryable),
    retry: () => retry.mutate(),
  };
}

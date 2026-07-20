import type { EntityType } from "../types/domain";
import type { DifferenceFilters } from "./reconciliation";

export const queryKeys = {
  task: (taskId: string) => ["reconciliation-task", taskId] as const,
  differences: (taskId: string, filters: DifferenceFilters) => ["differences", taskId, filters] as const,
  analysis: (differenceId: string) => ["analysis", differenceId] as const,
  editorSchema: (entityType: EntityType) => ["editor-schema", entityType] as const,
  proposals: (differenceId: string) => ["proposals", differenceId] as const,
  analysisJob: (jobId: string) => ["analysis-job", jobId] as const,
  analysisSummary: (taskId: string) => ["analysis-summary", taskId] as const,
};

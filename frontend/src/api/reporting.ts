import { requestJson } from "./client";

export interface ExecutionSummary {
  id: string;
  task_id: string;
  plan_id: string;
  plan_version: number;
  status: string;
  confirmed_by: string;
  confirmed_at: string;
  operation_count: number;
  retryable_count: number;
  output_target_version_id: string | null;
}

interface ExecutionOperation {
  record_id: string;
  operation_id: string;
  proposal_id: string;
  proposal_version: number;
  proposal_source: string;
  proposal_created_by: string;
  difference_id: string;
  difference_version: number;
  operation_type: string;
  entity_type: string;
  target_source_identifier: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  risk: string;
  attempts: Array<{
    attempt_number: number;
    status: string;
    error_code: string | null;
    actual_after: Record<string, unknown> | null;
    verification: Record<string, unknown> | null;
    retryable: boolean;
    target_version_id: string | null;
    created_at: string;
  }>;
}

export interface ExecutionDetail extends ExecutionSummary {
  source_snapshot_id: string;
  target_snapshot_id: string;
  input_target_version_id: string;
  output_target_version_ids: string[];
  operations: ExecutionOperation[];
  audit_events: Array<{
    id: string;
    operation_id: string | null;
    actor_id: string;
    event_type: string;
    details: Record<string, unknown>;
    created_at: string;
  }>;
  permitted_actions: string[];
}

export interface GovernanceReport {
  id: string;
  job_id: string;
  execution_id: string;
  version: number;
  facts_hash: string;
  facts: {
    input_target_version_id: string;
    output_target_version_ids: string[];
    restore_state: string;
  };
  content: { summary: string; restore_state?: string };
  provenance: Record<string, unknown>;
  generated_by: string;
  generated_at: string;
}

export interface TargetVersion {
  id: string;
  parent_version_id: string | null;
  task_id: string;
  batch_id: string | null;
  content_hash: string;
  created_at: string;
}

export interface RestorePreview {
  task_id: string;
  restore_request_id: string;
  source_version_id: string;
  semantic_source_version_id: string;
  target_version_id: string;
  preview_hash: string;
  allowed: boolean;
  conflicts: Array<{ code: string; message: string; operation_id: string | null }>;
  operations: Array<{
    id: string;
    operation_type: string;
    entity_type: string;
    target_source_identifier: string | null;
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
    dependencies: string[];
    risk: string;
    compensation_for: string | null;
  }>;
  covered_execution_ids: string[];
  explanation: string | null;
  explanation_state: string;
}

export interface RestoreConfirmation {
  restore_request_id: string;
  batch_id: string;
  plan_id: string;
  input_target_version_id: string;
  confirmed_by: string;
  status: string;
}

export interface ExecutionBatchResult {
  id: string;
  status: string;
  output_target_version_id: string | null;
}

const get = <T>(path: string) => requestJson<T>(path);
const post = <T>(path: string, body?: unknown, headers: Record<string, string> = {}) =>
  requestJson<T>(path, {
    method: "POST",
    headers: {
      ...headers,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const reportingApi = {
  listExecutions: () => get<{ items: ExecutionSummary[] }>("/api/execution-records?limit=100"),
  execution: (id: string) => get<ExecutionDetail>(`/api/execution-records/${id}`),
  reports: (id: string) => get<GovernanceReport[]>(`/api/execution-records/${id}/reports`),
  generateReport: (id: string) =>
    post<GovernanceReport>(`/api/execution-records/${id}/reports`, undefined, {
      "Idempotency-Key": crypto.randomUUID(),
    }),
  reportHtmlUrl: (id: string) => `/api/reports/${id}/html`,
  reportDownloadUrl: (id: string) => `/api/reports/${id}/download`,
  versions: (taskId: string) =>
    get<TargetVersion[]>(`/api/reconciliation-tasks/${taskId}/target-versions`),
  previewRestore: (versionId: string) =>
    post<RestorePreview>(`/api/target-versions/${versionId}/restore-preview`),
  confirmRestore: (previewHash: string, idempotencyKey: string = crypto.randomUUID()) =>
    post<RestoreConfirmation>(
      "/api/restores",
      { preview_hash: previewHash, high_risk_acknowledged: true },
      { "Idempotency-Key": idempotencyKey },
    ),
  executeRestore: (restoreRequestId: string) =>
    post<ExecutionBatchResult>(`/api/restores/${restoreRequestId}/execute`),
};

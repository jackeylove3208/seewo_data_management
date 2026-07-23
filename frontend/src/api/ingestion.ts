import { requestJson } from "./client";
import type { EntityType } from "../types/domain";
import type { WorkflowState } from "./reconciliation";

export interface UploadResponse {
  id: string;
  source_role: "authoritative" | "target";
  original_name: string;
  size_bytes: number;
  detected_encoding: string;
}

export interface SnapshotSummary {
  accepted: number;
  normalized_with_warning: number;
  quarantined: number;
  rejected: number;
  quarantine_available: boolean;
}

export interface ReconciliationTaskResponse {
  id: string;
  tenant_id: string;
  scope_id: string;
  status: string;
  stage: string;
  entity_types: EntityType[];
  snapshots: Record<"authoritative" | "target", SnapshotSummary>;
  workflow: WorkflowState;
  error: { message?: string } | null;
}

async function upload(file: File, sourceRole: "authoritative" | "target") {
  const form = new FormData();
  form.append("file", file);
  form.append("source_role", sourceRole);
  return requestJson<UploadResponse>("/api/uploads", { method: "POST", body: form });
}

function getTask(taskId: string, signal?: AbortSignal) {
  return requestJson<ReconciliationTaskResponse>(`/api/reconciliation-tasks/${taskId}`, { signal });
}

function readiness() {
  return requestJson<{ status: string }>("/health/ready");
}

function deleteTask(taskId: string) {
  return requestJson<void>(`/api/reconciliation-tasks/${taskId}`, { method: "DELETE" });
}

export const ingestionApi = { upload, getTask, readiness, deleteTask };

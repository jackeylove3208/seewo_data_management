import { requestJson } from "./client";
import type { EntityType } from "../types/domain";

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
  error: { message?: string } | null;
}

async function upload(file: File, sourceRole: "authoritative" | "target") {
  const form = new FormData();
  form.append("file", file);
  form.append("source_role", sourceRole);
  return requestJson<UploadResponse>("/api/uploads", { method: "POST", body: form });
}

function getTask(taskId: string) {
  return requestJson<ReconciliationTaskResponse>(`/api/reconciliation-tasks/${taskId}`);
}

function createTask(body: Record<string, unknown>, idempotencyKey: string = crypto.randomUUID()) {
  return requestJson<ReconciliationTaskResponse>("/api/reconciliation-tasks", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(body),
  });
}

function readiness() {
  return requestJson<{ status: string }>("/health/ready");
}

export const ingestionApi = { upload, getTask, createTask, readiness };

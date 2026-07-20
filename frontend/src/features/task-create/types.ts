import type { EntityType } from "../../types/domain";
import type { CsvSummary } from "./csvSummary";

export type SnapshotMode = "full" | "partial";
export type ConversationState = "idle" | "collecting" | "needs-input" | "draft-ready" | "submitting" | "failed" | "created";

export interface DraftAttachment {
  file: File;
  summary?: CsvSummary;
  error?: string;
}

export interface TaskIntentDraft {
  title: string;
  scopeLabel: string;
  snapshotMode: SnapshotMode;
  entityTypes: EntityType[];
}

export interface ManualSyncDraft extends TaskIntentDraft {
  source?: DraftAttachment;
  target?: DraftAttachment;
}

export type TaskDraft = ManualSyncDraft;

export interface ConversationMessage {
  id: string;
  role: "assistant" | "user";
  text: string;
  kind?: "normal" | "guardrail" | "error";
}

export interface AssistantRequest {
  draft: TaskIntentDraft;
  message: string;
}

export interface AssistantResponse {
  kind: "normal" | "guardrail";
  message: string;
  patch: Partial<TaskIntentDraft>;
}

export interface TaskCreationAssistant {
  respond(request: AssistantRequest): Promise<AssistantResponse>;
}

const entityTypes: EntityType[] = ["organization_unit", "class", "teacher", "student"];
const snapshotModes: SnapshotMode[] = ["full", "partial"];

export function isTaskIntentDraft(value: unknown): value is TaskIntentDraft {
  if (!value || typeof value !== "object") return false;
  const draft = value as Record<string, unknown>;
  return typeof draft.title === "string"
    && typeof draft.scopeLabel === "string"
    && snapshotModes.includes(draft.snapshotMode as SnapshotMode)
    && Array.isArray(draft.entityTypes)
    && draft.entityTypes.every((entityType) => entityTypes.includes(entityType as EntityType));
}

export function isAssistantResponse(value: unknown): value is AssistantResponse {
  if (!value || typeof value !== "object") return false;
  const response = value as Record<string, unknown>;
  if (!(["normal", "guardrail"] as const).includes(response.kind as AssistantResponse["kind"])
    || typeof response.message !== "string"
    || !response.patch
    || typeof response.patch !== "object"
    || Array.isArray(response.patch)) return false;
  const patch = response.patch as Record<string, unknown>;
  const allowedKeys = ["title", "scopeLabel", "snapshotMode", "entityTypes"];
  if (Object.keys(patch).some((key) => !allowedKeys.includes(key))) return false;
  return (patch.title === undefined || typeof patch.title === "string")
    && (patch.scopeLabel === undefined || typeof patch.scopeLabel === "string")
    && (patch.snapshotMode === undefined || snapshotModes.includes(patch.snapshotMode as SnapshotMode))
    && (patch.entityTypes === undefined || (
      Array.isArray(patch.entityTypes)
      && patch.entityTypes.every((entityType) => entityTypes.includes(entityType as EntityType))
    ));
}

export function isTaskIntentReady(draft: TaskIntentDraft) {
  return Boolean(
    draft.title.trim()
    && draft.scopeLabel.trim()
    && draft.entityTypes.length > 0,
  );
}

export function isDraftReady(draft: ManualSyncDraft) {
  return Boolean(
    isTaskIntentReady(draft)
    && draft.source?.summary
    && draft.target?.summary,
  );
}

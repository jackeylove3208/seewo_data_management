import type { EntityType } from "../../types/domain";
import type { CsvSummary } from "./csvSummary";

export type SnapshotMode = "full" | "partial";
export type ConversationState = "idle" | "collecting" | "needs-input" | "draft-ready" | "submitting" | "failed" | "created";

export interface DraftAttachment {
  file: File;
  summary?: CsvSummary;
  error?: string;
}

export interface TaskDraft {
  title: string;
  scopeLabel: string;
  snapshotMode: SnapshotMode;
  entityTypes: EntityType[];
  source?: DraftAttachment;
  target?: DraftAttachment;
}

export interface ConversationMessage {
  id: string;
  role: "assistant" | "user";
  text: string;
  kind?: "normal" | "guardrail" | "error";
}

export interface AssistantRequest {
  draft: TaskDraft;
  message: string;
}

export interface AssistantResponse {
  kind: "normal" | "guardrail";
  message: string;
  patch: Partial<Pick<TaskDraft, "title" | "scopeLabel" | "snapshotMode" | "entityTypes">>;
}

export interface TaskCreationAssistant {
  respond(request: AssistantRequest): Promise<AssistantResponse>;
}

export function isDraftReady(draft: TaskDraft) {
  return Boolean(
    draft.title.trim()
    && draft.scopeLabel.trim()
    && draft.entityTypes.length > 0
    && draft.source?.summary
    && draft.target?.summary,
  );
}

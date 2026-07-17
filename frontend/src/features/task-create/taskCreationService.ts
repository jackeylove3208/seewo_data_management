import { ingestionApi } from "../../api/ingestion";
import { saveStoredTask } from "../../data/taskHistory";
import type { TaskHistoryItem } from "../../types/domain";
import type { TaskDraft } from "./types";
import { isDraftReady } from "./types";

type CreationApi = Pick<typeof ingestionApi, "upload" | "createTask">;

interface CreationDependencies {
  api?: CreationApi;
  saveTask?: (task: TaskHistoryItem) => void;
}

export async function createTaskFromDraft(
  draft: TaskDraft,
  idempotencyKey: string,
  dependencies: CreationDependencies = {},
) {
  if (!isDraftReady(draft) || !draft.source?.summary || !draft.target?.summary) {
    throw new Error("任务草案尚未完整");
  }
  const api = dependencies.api ?? ingestionApi;
  const persistTask = dependencies.saveTask ?? saveStoredTask;
  const [sourceUpload, targetUpload] = await Promise.all([
    api.upload(draft.source.file, "authoritative"),
    api.upload(draft.target.file, "target"),
  ]);
  const task = await api.createTask({
    authoritative_upload_id: sourceUpload.id,
    target_upload_id: targetUpload.id,
    scope_id: draft.scopeLabel,
    snapshot_mode: draft.snapshotMode,
    entity_types: draft.entityTypes,
    schema_version: "canonical-v1",
    authoritative_mapping_version: "third-party-v1",
    target_mapping_version: "mofa-v1",
  }, idempotencyKey);

  persistTask({
    id: task.id,
    title: draft.title,
    createdAt: new Date().toISOString(),
    sourceFile: draft.source.file.name,
    targetFile: draft.target.file.name,
    sourceAccepted: task.snapshots.authoritative.accepted,
    targetAccepted: task.snapshots.target.accepted,
    issueCount: 0,
    status: task.status === "ready" ? "ready" : task.status === "failed" ? "failed" : "processing",
    selectedEntityTypes: draft.entityTypes,
    entityCounts: Object.fromEntries(draft.entityTypes.map((type) => [
      type,
      { source: draft.source?.summary?.counts[type] ?? 0, target: draft.target?.summary?.counts[type] ?? 0 },
    ])),
  });
  return task;
}

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

export interface TaskCreationAttempt {
  draft: TaskDraft;
  run: () => ReturnType<typeof ingestionApi.createTask>;
}

export function createTaskAttempt(
  draft: TaskDraft,
  idempotencyKey: string,
  dependencies: CreationDependencies = {},
): TaskCreationAttempt {
  if (!isDraftReady(draft) || !draft.source?.summary || !draft.target?.summary) {
    throw new Error("任务草案尚未完整");
  }
  const api = dependencies.api ?? ingestionApi;
  const persistTask = dependencies.saveTask ?? saveStoredTask;
  let sourceUpload: ReturnType<CreationApi["upload"]> | undefined;
  let targetUpload: ReturnType<CreationApi["upload"]> | undefined;
  let request: Parameters<CreationApi["createTask"]>[0] | undefined;

  function upload(role: "source" | "target") {
    const attachment = role === "source" ? draft.source! : draft.target!;
    const sourceRole = role === "source" ? "authoritative" : "target";
    const current = role === "source" ? sourceUpload : targetUpload;
    if (current) return current;
    const pending = api.upload(attachment.file, sourceRole).catch((error) => {
      if (role === "source") sourceUpload = undefined;
      else targetUpload = undefined;
      throw error;
    });
    if (role === "source") sourceUpload = pending;
    else targetUpload = pending;
    return pending;
  }

  async function run() {
    if (!request) {
      const [source, target] = await Promise.all([upload("source"), upload("target")]);
      request = {
        authoritative_upload_id: source.id,
        target_upload_id: target.id,
        scope_id: draft.scopeLabel,
        snapshot_mode: draft.snapshotMode,
        entity_types: draft.entityTypes,
        schema_version: "canonical-v1",
        authoritative_mapping_version: "third-party-v1",
        target_mapping_version: "mofa-v1",
      };
    }
    const task = await api.createTask(request, idempotencyKey);
    persistTask({
      id: task.id,
      title: draft.title,
      createdAt: new Date().toISOString(),
      sourceFile: draft.source!.file.name,
      targetFile: draft.target!.file.name,
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

  return { draft, run };
}

export async function createTaskFromDraft(
  draft: TaskDraft,
  idempotencyKey: string,
  dependencies: CreationDependencies = {},
) {
  return createTaskAttempt(draft, idempotencyKey, dependencies).run();
}

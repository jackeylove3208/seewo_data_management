import type { TaskHistoryItem } from "../types/domain";
import type { AgentHistoryItem } from "../api/agent";

const STORAGE_KEY = "mofa-reconciliation-tasks";
export const TASK_HISTORY_UPDATED_EVENT = "mofa-task-history-updated";
export const UNKNOWN_TARGET_SOURCE_KEY = "unknown-history-source";

export interface TargetSourceTaskGroup {
  key: string;
  name: string;
  kind: "database" | "local" | "upload" | "unknown";
  identified: boolean;
  lastActivityAt: string;
  taskCount: number;
  processingCount: number;
  failedCount: number;
  pendingIssueCount: number;
  tasks: TaskHistoryItem[];
}

export function toTaskHistoryItem(item: AgentHistoryItem): TaskHistoryItem {
  const targetSource = item.target_source ?? {
    key: UNKNOWN_TARGET_SOURCE_KEY,
    name: "其他历史任务",
    kind: "unknown" as const,
    identified: false,
  };
  return {
    id: item.id,
    title: item.title ?? (item.task_kind === "rollback" ? "回滚任务" : "Agent 同步任务"),
    createdAt: item.created_at,
    sourceFile: item.task_kind === "rollback" ? "治理执行事实" : "后端连接器",
    targetFile: targetSource.name,
    sourceAccepted: 0,
    targetAccepted: 0,
    issueCount: item.issue_summary.total,
    status: item.termination_requested || item.status === "terminated"
      ? "terminated"
      : item.status === "completed"
        ? "ready"
        : item.status === "failed"
          ? "failed"
          : "processing",
    selectedEntityTypes: (item.entity_types ?? []).map((type) => type === "department" ? "organization_unit" : type),
    workflowVersion: item.workflow_version,
    taskKind: item.task_kind,
    reportId: item.report_id,
    rollbackEligible: item.rollback_eligible,
    deletionEligible: item.deletion_eligible,
    operationSummary: item.operation_summary,
    targetSourceKey: targetSource.key,
    targetSourceName: targetSource.name,
    targetSourceKind: targetSource.kind,
    targetSourceIdentified: targetSource.identified,
  };
}

export function groupTasksByTargetSource(
  tasks: readonly TaskHistoryItem[],
): TargetSourceTaskGroup[] {
  const ordered = [...tasks].sort(compareTasksNewestFirst);
  const grouped = new Map<string, TaskHistoryItem[]>();
  for (const task of ordered) {
    const key = task.targetSourceKey ?? UNKNOWN_TARGET_SOURCE_KEY;
    const members = grouped.get(key);
    if (members) members.push(task);
    else grouped.set(key, [task]);
  }
  return [...grouped.entries()]
    .map(([key, members]) => {
      const first = members[0];
      return {
        key,
        name: first.targetSourceName ?? "其他历史任务",
        kind: first.targetSourceKind ?? "unknown",
        identified: first.targetSourceIdentified ?? false,
        lastActivityAt: first.createdAt,
        taskCount: members.length,
        processingCount: members.filter((task) => task.status === "processing").length,
        failedCount: members.filter((task) => task.status === "failed").length,
        pendingIssueCount: members
          .filter((task) => task.status !== "ready")
          .reduce((sum, task) => sum + task.issueCount, 0),
        tasks: members,
      } satisfies TargetSourceTaskGroup;
    })
    .sort((left, right) => (
      Date.parse(right.lastActivityAt) - Date.parse(left.lastActivityAt)
      || left.name.localeCompare(right.name, "zh-CN")
      || left.key.localeCompare(right.key)
    ));
}

export function getStoredTasks(): TaskHistoryItem[] {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value ? (JSON.parse(value) as TaskHistoryItem[]) : [];
  } catch {
    return [];
  }
}

export function saveStoredTask(task: TaskHistoryItem) {
  const tasks = getStoredTasks().filter((item) => item.id !== task.id);
  localStorage.setItem(STORAGE_KEY, JSON.stringify([task, ...tasks]));
  window.dispatchEvent(new Event(TASK_HISTORY_UPDATED_EVENT));
}

export function removeStoredTask(taskId: string) {
  const tasks = getStoredTasks().filter((item) => item.id !== taskId);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
  window.dispatchEvent(new Event(TASK_HISTORY_UPDATED_EVENT));
}

export function findTask(taskId: string) {
  return getStoredTasks().find((task) => task.id === taskId);
}

export function allTasks() {
  return getStoredTasks().sort(
    (left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt),
  );
}

function compareTasksNewestFirst(left: TaskHistoryItem, right: TaskHistoryItem) {
  return Date.parse(right.createdAt) - Date.parse(left.createdAt)
    || left.id.localeCompare(right.id);
}

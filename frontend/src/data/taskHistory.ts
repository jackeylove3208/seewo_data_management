import type { TaskHistoryItem } from "../types/domain";
import type { AgentHistoryItem } from "../api/agent";

const STORAGE_KEY = "mofa-reconciliation-tasks";
export const TASK_HISTORY_UPDATED_EVENT = "mofa-task-history-updated";

export function toTaskHistoryItem(item: AgentHistoryItem): TaskHistoryItem {
  return {
    id: item.id,
    title: item.title ?? (item.task_kind === "rollback" ? "回滚任务" : "Agent 同步任务"),
    createdAt: item.created_at,
    sourceFile: item.task_kind === "rollback" ? "治理执行事实" : "后端连接器",
    targetFile: item.task_kind === "rollback" ? "目标恢复版本" : "希沃目标",
    sourceAccepted: 0,
    targetAccepted: 0,
    issueCount: item.issue_summary.total,
    status: ["completed", "terminated"].includes(item.status) ? "ready" : item.status === "failed" ? "failed" : "processing",
    selectedEntityTypes: (item.entity_types ?? []).map((type) => type === "department" ? "organization_unit" : type),
    workflowVersion: item.workflow_version,
    taskKind: item.task_kind,
    reportId: item.report_id,
    rollbackEligible: item.rollback_eligible,
    deletionEligible: item.deletion_eligible,
  };
}

export const demoTasks: TaskHistoryItem[] = [
  {
    id: "demo-001",
    title: "三方全校数据核对",
    createdAt: "2026-07-16T10:32:00+08:00",
    sourceFile: "third_party_data.csv",
    targetFile: "mofa_data.csv",
    sourceAccepted: 515,
    targetAccepted: 518,
    issueCount: 9,
    status: "ready",
    selectedEntityTypes: ["organization_unit", "class", "teacher", "student"],
    isDemo: true,
  },
  {
    id: "demo-002",
    title: "高中部教师数据核对",
    createdAt: "2026-07-15T16:18:00+08:00",
    sourceFile: "高中部教师名单.csv",
    targetFile: "魔方教师导出.csv",
    sourceAccepted: 86,
    targetAccepted: 88,
    issueCount: 4,
    status: "ready",
    selectedEntityTypes: ["teacher"],
    isDemo: true,
  },
];

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
  return [...getStoredTasks(), ...demoTasks].find((task) => task.id === taskId);
}

export function allTasks() {
  return [...getStoredTasks(), ...demoTasks].sort(
    (left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt),
  );
}

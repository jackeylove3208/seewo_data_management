import type { TaskHistoryItem } from "../types/domain";

const STORAGE_KEY = "mofa-reconciliation-tasks";
export const TASK_HISTORY_UPDATED_EVENT = "mofa-task-history-updated";

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

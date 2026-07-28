import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  allTasks,
  findTask,
  getStoredTasks,
  removeStoredTask,
  saveStoredTask,
  TASK_HISTORY_UPDATED_EVENT,
  toTaskHistoryItem,
} from "./taskHistory";

const baseTask = {
  title: "任务",
  createdAt: "2026-07-20T08:00:00Z",
  sourceFile: "source.csv",
  targetFile: "target.csv",
  sourceAccepted: 1,
  targetAccepted: 1,
  issueCount: 0,
  status: "ready" as const,
  selectedEntityTypes: ["teacher" as const],
};

describe("task history removal", () => {
  beforeEach(() => localStorage.clear());

  it("does not inject obsolete demonstration tasks into real history", () => {
    expect(allTasks()).toEqual([]);
  });

  it("does not resolve removed demonstration task IDs", () => {
    expect(findTask("demo-001")).toBeUndefined();
    expect(findTask("demo-002")).toBeUndefined();
  });

  it("removes one stored task and publishes a history update", () => {
    saveStoredTask({ ...baseTask, id: "task-2", title: "保留任务" });
    saveStoredTask({ ...baseTask, id: "task-1", title: "删除任务" });
    const listener = vi.fn();
    window.addEventListener(TASK_HISTORY_UPDATED_EVENT, listener);

    removeStoredTask("task-1");

    expect(getStoredTasks().map((task) => task.id)).toEqual(["task-2"]);
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(TASK_HISTORY_UPDATED_EVENT, listener);
  });

  it("maps an approved termination request to a terminated history state", () => {
    const task = toTaskHistoryItem({
      id: "rollback-task",
      workflow_version: "agent-graph-v1",
      task_kind: "rollback",
      parent_task_id: "source-task",
      phase: "plan_restore",
      status: "running",
      title: "回滚任务",
      report_id: null,
      rollback_eligible: false,
      deletion_eligible: true,
      created_at: "2026-07-27T08:00:00Z",
      completed_at: null,
      termination_requested: true,
      issue_summary: { total: 0, excluded: 0 },
      operation_summary: { succeeded: 0, failed: 0, blocked: 0 },
      entity_types: ["student"],
    });

    expect(task.status).toBe("terminated");
  });
});

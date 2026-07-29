import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  allTasks,
  findTask,
  getStoredTasks,
  groupTasksByTargetSource,
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
      target_source: {
        key: "target-source-1",
        name: "希沃组织主库",
        kind: "database",
        identified: true,
      },
    });

    expect(task.status).toBe("terminated");
    expect(task.targetSourceKey).toBe("target-source-1");
    expect(task.targetSourceName).toBe("希沃组织主库");
  });
});

describe("task history target source grouping", () => {
  it("puts sync and rollback tasks for one target into one newest-first group", () => {
    const groups = groupTasksByTargetSource([
      {
        ...baseTask,
        id: "sync-old",
        taskKind: "sync",
        targetSourceKey: "source-a",
        targetSourceName: "希沃组织主库",
        targetSourceKind: "database",
        targetSourceIdentified: true,
      },
      {
        ...baseTask,
        id: "rollback-new",
        taskKind: "rollback",
        createdAt: "2026-07-22T08:00:00Z",
        targetSourceKey: "source-a",
        targetSourceName: "希沃组织主库",
        targetSourceKind: "database",
        targetSourceIdentified: true,
      },
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].taskCount).toBe(2);
    expect(groups[0].tasks.map((task) => task.id)).toEqual([
      "rollback-new",
      "sync-old",
    ]);
    expect(groups[0].lastActivityAt).toBe("2026-07-22T08:00:00Z");
  });

  it("keeps equal display names separate when stable source keys differ", () => {
    const groups = groupTasksByTargetSource([
      {
        ...baseTask,
        id: "first-upload",
        targetSourceKey: "upload-a",
        targetSourceName: "临时上传 · students.csv",
        targetSourceKind: "upload",
        targetSourceIdentified: true,
      },
      {
        ...baseTask,
        id: "second-upload",
        targetSourceKey: "upload-b",
        targetSourceName: "临时上传 · students.csv",
        targetSourceKind: "upload",
        targetSourceIdentified: true,
      },
    ]);

    expect(groups.map((group) => group.key)).toEqual(["upload-a", "upload-b"]);
  });

  it("places old cached tasks without source facts in the unknown group", () => {
    const groups = groupTasksByTargetSource([
      { ...baseTask, id: "legacy-task" },
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({
      key: "unknown-history-source",
      name: "其他历史任务",
      kind: "unknown",
      identified: false,
    });
  });
});

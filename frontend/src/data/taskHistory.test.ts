import { beforeEach, describe, expect, it, vi } from "vitest";

import { getStoredTasks, removeStoredTask, saveStoredTask, TASK_HISTORY_UPDATED_EVENT } from "./taskHistory";

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
});

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { agentApi } from "../../api/agent";
import { ingestionApi } from "../../api/ingestion";
import { getStoredTasks, saveStoredTask } from "../../data/taskHistory";
import type { TaskHistoryItem } from "../../types/domain";
import { useTaskDeletion } from "./useTaskDeletion";

const task: TaskHistoryItem = {
  id: "task-1",
  title: "七年级教师核对",
  createdAt: "2026-07-20T08:00:00Z",
  sourceFile: "source.csv",
  targetFile: "target.csv",
  sourceAccepted: 1,
  targetAccepted: 1,
  issueCount: 0,
  status: "ready",
  selectedEntityTypes: ["teacher"],
};

function Harness({ selectedTask = task }: { selectedTask?: TaskHistoryItem }) {
  const deletion = useTaskDeletion();
  return (
    <>
      <button type="button" onClick={() => deletion.requestDelete(selectedTask)}>删除真实任务</button>
      {deletion.confirmation}
    </>
  );
}

describe("task deletion confirmation", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    saveStoredTask(task);
  });

  it("cancels without calling the backend", async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(ingestionApi, "deleteTask").mockResolvedValue();
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "删除真实任务" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("七年级教师核对");
    await user.click(screen.getByRole("button", { name: /取\s*消/ }));

    expect(request).not.toHaveBeenCalled();
    expect(getStoredTasks()).toHaveLength(1);
  });

  it("removes local history only after backend deletion succeeds", async () => {
    const user = userEvent.setup();
    let resolve!: () => void;
    vi.spyOn(ingestionApi, "deleteTask").mockReturnValue(new Promise<void>((done) => { resolve = done; }));
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "删除真实任务" }));
    await user.click(screen.getByRole("button", { name: "确认删除" }));
    expect(getStoredTasks()).toHaveLength(1);
    expect(screen.getByRole("button", { name: /确认删除/ })).toBeDisabled();

    resolve();
    await waitFor(() => expect(getStoredTasks()).toHaveLength(0));
  });

  it("keeps history and explains a protected task", async () => {
    const user = userEvent.setup();
    vi.spyOn(ingestionApi, "deleteTask").mockRejectedValue(new Error("该任务已有治理执行记录，不能删除"));
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "删除真实任务" }));
    await user.click(screen.getByRole("button", { name: "确认删除" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("该任务已有治理执行记录，不能删除");
    expect(getStoredTasks()).toHaveLength(1);
  });

  it("deletes controlled graph tasks through the Agent API", async () => {
    const user = userEvent.setup();
    const graphTask = {
      ...task,
      id: "graph-task-1",
      workflowVersion: "agent-graph-v1",
    };
    saveStoredTask(graphTask);
    const deleteAgentTask = vi.spyOn(agentApi, "deleteTask").mockResolvedValue();
    const deleteLegacyTask = vi.spyOn(ingestionApi, "deleteTask").mockResolvedValue();
    render(<Harness selectedTask={graphTask} />);

    await user.click(screen.getByRole("button", { name: "删除真实任务" }));
    await user.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(deleteAgentTask).toHaveBeenCalledWith("graph-task-1"));
    expect(deleteLegacyTask).not.toHaveBeenCalled();
  });
});

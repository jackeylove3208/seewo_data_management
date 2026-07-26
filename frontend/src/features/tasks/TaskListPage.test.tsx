import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { TaskListPage } from "./TaskListPage";
import { saveStoredTask } from "../../data/taskHistory";

function LocationProbe() {
  return <output>{useLocation().pathname}</output>;
}

describe("task history", () => {
  beforeEach(() => localStorage.clear());

  it("opens external data sync from the task list", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <Routes>
          <Route path="/tasks" element={<TaskListPage />} />
          <Route path="/tasks/new" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "外部数据同步" }));
    expect(screen.getByText("/tasks/new")).toBeInTheDocument();
  });

  it("shows only real task rows and allows an eligible task to be deleted", () => {
    saveStoredTask({
      id: "real-task",
      title: "真实分析任务",
      createdAt: "2026-07-20T08:00:00Z",
      sourceFile: "source.csv",
      targetFile: "target.csv",
      sourceAccepted: 1,
      targetAccepted: 1,
      issueCount: 0,
      status: "ready",
      selectedEntityTypes: ["teacher"],
    });
    render(<MemoryRouter><TaskListPage /></MemoryRouter>);

    expect(screen.getByRole("button", { name: "删除真实分析任务" })).toBeInTheDocument();
    expect(screen.queryByText("三方全校数据核对")).not.toBeInTheDocument();
    expect(screen.queryByText("高中部教师数据核对")).not.toBeInTheDocument();
  });
});

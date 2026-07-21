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

  it("opens a historical task when its row body is clicked", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <Routes>
          <Route path="/tasks" element={<TaskListPage />} />
          <Route path="/tasks/:taskId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByText("三方全校数据核对"));

    expect(screen.getByText("/tasks/demo-001")).toBeInTheDocument();
  });

  it("shows deletion only for real task rows", () => {
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
    expect(screen.queryByRole("button", { name: "删除三方全校数据核对" })).not.toBeInTheDocument();
  });
});

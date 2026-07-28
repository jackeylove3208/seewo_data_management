import { render, screen, within } from "@testing-library/react";
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

  it("keeps the recent-task heading inside its padded card header", () => {
    render(<MemoryRouter><TaskListPage /></MemoryRouter>);

    expect(screen.getByText("最近任务").parentElement).toHaveClass(
      "task-list-heading",
    );
    expect(screen.getByText("点击任意一行查看详情")).toBeInTheDocument();
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

  it("calculates sync completion rate and only counts unresolved task issues", () => {
    const tasks = [
      { id: "sync-completed", title: "已完成同步", status: "ready" as const, issueCount: 8, taskKind: "sync" as const },
      { id: "sync-running", title: "执行中同步", status: "processing" as const, issueCount: 3, taskKind: "sync" as const },
      { id: "sync-terminated", title: "已终止同步", status: "terminated" as const, issueCount: 4, taskKind: "sync" as const },
      { id: "sync-failed", title: "失败同步", status: "failed" as const, issueCount: 2, taskKind: "sync" as const },
      { id: "rollback-completed", title: "已完成回滚", status: "ready" as const, issueCount: 10, taskKind: "rollback" as const },
    ];
    tasks.forEach((task, index) => saveStoredTask({
      ...task,
      createdAt: `2026-07-${20 + index}T08:00:00Z`,
      sourceFile: "后端连接器",
      targetFile: "希沃目标",
      sourceAccepted: 0,
      targetAccepted: 0,
      selectedEntityTypes: ["student"],
    }));

    render(<MemoryRouter><TaskListPage /></MemoryRouter>);

    expect(
      within(screen.getByText("待处理问题").parentElement!).getByText("9"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByText("治理操作成功率").parentElement!).getByText("25%"),
    ).toBeInTheDocument();
    expect(screen.getByText("已终止同步").closest(".task-row")).toHaveTextContent("已终止");
  });
});

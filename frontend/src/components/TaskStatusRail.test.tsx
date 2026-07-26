import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { TaskStatusRail } from "./TaskStatusRail";

const stages = [
  { id: "ingest", label: "数据接入" },
  { id: "analysis", label: "Agent 分析与决策" },
  { id: "governance", label: "治理执行" },
  { id: "generate_report", label: "报告生成" },
];

describe("TaskStatusRail", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("persists its independent collapsed preference", async () => {
    const user = userEvent.setup();
    const first = render(
      <TaskStatusRail stages={stages} currentIndex={1} />,
    );

    await user.click(
      screen.getByRole("button", { name: "收起任务处理状态" }),
    );

    expect(localStorage.getItem("mofa-task-status-collapsed")).toBe("true");
    expect(
      screen.getByRole("button", { name: "展开任务处理状态" }),
    ).toBeVisible();
    first.unmount();

    render(<TaskStatusRail stages={stages} currentIndex={1} />);
    expect(
      screen.getByRole("button", { name: "展开任务处理状态" }),
    ).toBeVisible();
    expect(screen.getByText("Agent 分析与决策")).toBeInTheDocument();
  });

  it("announces completed active blocked and termination-report states", () => {
    const { rerender } = render(
      <TaskStatusRail stages={stages} currentIndex={2} blocked />,
    );

    expect(screen.getByText("数据接入").closest("li")).toHaveAttribute(
      "data-status",
      "completed",
    );
    expect(screen.getByText("治理执行").closest("li")).toHaveAttribute(
      "data-status",
      "blocked",
    );
    expect(screen.getByText("分析已暂停")).toBeInTheDocument();

    rerender(
      <TaskStatusRail
        stages={stages}
        currentIndex={3}
        terminationRequested
      />,
    );
    expect(screen.getByText("生成终止报告")).toBeInTheDocument();
    expect(screen.getByText("正在处理")).toBeInTheDocument();
  });
});

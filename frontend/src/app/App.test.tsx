import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

import { App } from "./App";

describe("application shell", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("shows the persistent workspace and opens a fresh reconciliation", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    window.history.pushState({}, "", "/tasks/demo-001");
    render(<App />);

    expect(screen.getByRole("link", { name: "魔方 AI 数据治理" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "对账工作区" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /三方全校数据核对/ })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByText("后端未连接")).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "外部数据同步" }));
    expect(screen.getByRole("heading", { name: "外部数据同步" })).toBeInTheDocument();
  });

  it("opens a fresh conversation without changing task history", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const history = JSON.stringify([{
      id: "history-1",
      title: "历史任务",
      createdAt: "2026-07-20T08:00:00Z",
      sourceFile: "source.csv",
      targetFile: "target.csv",
      sourceAccepted: 1,
      targetAccepted: 1,
      issueCount: 0,
      status: "ready",
      selectedEntityTypes: ["teacher"],
    }]);
    localStorage.setItem("mofa-reconciliation-tasks", history);
    window.history.pushState({}, "", "/tasks/demo-001");
    render(<App />);

    await user.click(screen.getByRole("link", { name: "新建对话" }));

    expect(screen.getByRole("heading", { name: "新建对话" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "新建对话" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "任务草案" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "继续外部数据同步" })).not.toBeInTheDocument();
    expect(window.location.pathname).toBe("/conversations/new");
    expect(localStorage.getItem("mofa-reconciliation-tasks")).toBe(history);
  });

  it("moves focus into and back from the mobile workspace", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    window.history.pushState({}, "", "/tasks");
    render(<App />);

    const openButton = screen.getByRole("button", { name: "打开导航" });
    await user.click(openButton);
    const closeButton = within(screen.getByRole("navigation", { name: "对账工作区" })).getByRole("button", { name: "关闭导航" });
    expect(closeButton).toHaveFocus();

    await user.click(closeButton);
    expect(openButton).toHaveFocus();
  });
});

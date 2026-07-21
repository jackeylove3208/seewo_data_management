import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, vi } from "vitest";

import { WorkspaceSidebar } from "./WorkspaceSidebar";
import { saveStoredTask } from "../data/taskHistory";

describe("workspace sidebar", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows recent task summaries and highlights the current task", async () => {
    render(
      <MemoryRouter initialEntries={["/tasks/demo-001"]}>
        <WorkspaceSidebar mobileOpen={false} onMobileClose={() => undefined} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: /三方全校数据核对/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("9 个问题")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看全部历史" })).toHaveAttribute("href", "/executions");
    expect(await screen.findByText("后端未连接")).toBeInTheDocument();
  });

  it("persists desktop collapse preference", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <WorkspaceSidebar mobileOpen={false} onMobileClose={() => undefined} />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "收起侧栏" }));

    expect(screen.getByRole("button", { name: "展开侧栏" })).toBeInTheDocument();
    expect(localStorage.getItem("mofa-workspace-collapsed")).toBe("true");
  });

  it("closes the mobile workspace after navigation", async () => {
    const user = userEvent.setup();
    let closed = false;
    render(
      <MemoryRouter initialEntries={["/tasks/new"]}>
        <WorkspaceSidebar mobileOpen onMobileClose={() => { closed = true; }} />
        <Routes><Route path="*" element={<div />} /></Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("link", { name: /三方全校数据核对/ }));
    expect(closed).toBe(true);
  });

  it("refreshes recent history after a task is saved", async () => {
    render(
      <MemoryRouter>
        <WorkspaceSidebar mobileOpen={false} onMobileClose={() => undefined} />
      </MemoryRouter>,
    );
    expect(await screen.findByText("后端未连接")).toBeInTheDocument();

    act(() => saveStoredTask({
      id: "new-task",
      title: "七年级教师核对",
      createdAt: new Date().toISOString(),
      sourceFile: "source.csv",
      targetFile: "target.csv",
      sourceAccepted: 10,
      targetAccepted: 10,
      issueCount: 0,
      status: "ready",
      selectedEntityTypes: ["teacher"],
    }));

    expect(screen.getByRole("link", { name: /七年级教师核对/ })).toBeInTheDocument();
  });
});

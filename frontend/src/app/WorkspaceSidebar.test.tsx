import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, vi } from "vitest";

import { WorkspaceSidebar } from "./WorkspaceSidebar";
import { agentApi } from "../api/agent";
import type { AgentHistoryItem } from "../api/agent";
import {
  saveStoredTask,
  TASK_HISTORY_UPDATED_EVENT,
} from "../data/taskHistory";

function historyItem(
  id: string,
  title: string,
  taskKind: "sync" | "rollback",
  sourceKey: string,
  sourceName: string,
): AgentHistoryItem {
  return {
    id,
    workflow_version: "agent-graph-v1",
    task_kind: taskKind,
    parent_task_id: taskKind === "rollback" ? "sync-task" : null,
    phase: "terminal",
    status: "completed",
    title,
    report_id: null,
    rollback_eligible: false,
    deletion_eligible: true,
    created_at: taskKind === "rollback"
      ? "2026-07-29T09:00:00Z"
      : "2026-07-28T09:00:00Z",
    completed_at: "2026-07-29T10:00:00Z",
    termination_requested: false,
    issue_summary: { total: 1, excluded: 0 },
    operation_summary: { succeeded: 1, failed: 0, blocked: 0 },
    entity_types: ["student"],
    target_source: {
      key: sourceKey,
      name: sourceName,
      kind: "database",
      identified: true,
    },
  };
}

describe("workspace sidebar", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the local app icon in the workspace brand", async () => {
    render(
      <MemoryRouter>
        <WorkspaceSidebar mobileOpen={false} onMobileClose={() => undefined} />
      </MemoryRouter>,
    );

    const brand = screen.getByRole("link", { name: "魔方 AI 数据治理" });
    expect(brand.querySelector("img")).toHaveAttribute(
      "src",
      expect.stringContaining("mofa-app-icon.png"),
    );
    expect(await screen.findByText("后端未连接")).toBeInTheDocument();
  });

  it("keeps only the conversation entry in the sidebar", async () => {
    render(
      <MemoryRouter initialEntries={["/conversations/new"]}>
        <WorkspaceSidebar mobileOpen={false} onMobileClose={() => undefined} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "新建对话" })).toHaveAttribute("href", "/conversations/new");
    expect(screen.getByRole("link", { name: "新建对话" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: "外部数据同步" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "新建对账" })).not.toBeInTheDocument();
    expect(await screen.findByText("后端未连接")).toBeInTheDocument();
  });

  it("keeps the conversation command named when the desktop workspace is collapsed", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/tasks/new"]}>
        <WorkspaceSidebar mobileOpen={false} onMobileClose={() => undefined} />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "收起侧栏" }));

    expect(screen.getByRole("link", { name: "新建对话" })).toHaveAttribute("title", "新建对话");
    expect(screen.queryByRole("link", { name: "外部数据同步" })).not.toBeInTheDocument();
  });

  it("does not show the two obsolete hard-coded demonstration tasks", async () => {
    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <WorkspaceSidebar mobileOpen={false} onMobileClose={() => undefined} />
      </MemoryRouter>,
    );

    expect(screen.queryByRole("link", { name: /三方全校数据核对/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /高中部教师数据核对/ })).not.toBeInTheDocument();
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

    await user.click(screen.getByRole("link", { name: "新建对话" }));
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

    await waitFor(() => {
      expect(screen.getByRole("link", { name: /七年级教师核对/ })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "删除七年级教师核对" })).toBeInTheDocument();
  });

  it("does not restore stale local tasks after authoritative history refresh", async () => {
    vi.spyOn(agentApi, "history").mockResolvedValue({
      items: [],
      next_cursor: null,
    });
    render(
      <MemoryRouter>
        <WorkspaceSidebar mobileOpen={false} onMobileClose={() => undefined} />
      </MemoryRouter>,
    );
    expect(await screen.findByText("后端未连接")).toBeInTheDocument();

    act(() => saveStoredTask({
      id: "stale-task",
      title: "已经从后端删除的旧任务",
      createdAt: new Date().toISOString(),
      sourceFile: "source.csv",
      targetFile: "target.csv",
      sourceAccepted: 0,
      targetAccepted: 0,
      issueCount: 0,
      status: "ready",
      selectedEntityTypes: ["teacher"],
      workflowVersion: "agent-graph-v1",
    }));

    await waitFor(() => {
      expect(screen.queryByRole("link", { name: /已经从后端删除的旧任务/ })).not.toBeInTheDocument();
    });
    expect(agentApi.history).toHaveBeenCalledTimes(2);
  });

  it("groups sync and rollback history by target source and expands the active group", async () => {
    vi.spyOn(agentApi, "history").mockResolvedValue({
      items: [
        historyItem("rollback-task", "回滚教师电话", "rollback", "source-a", "希沃组织主库"),
        historyItem("sync-task", "全校组织同步", "sync", "source-a", "希沃组织主库"),
        historyItem("other-task", "临时学生同步", "sync", "source-b", "另一希沃目标"),
      ],
      next_cursor: null,
    });

    render(
      <MemoryRouter initialEntries={["/tasks/rollback-task"]}>
        <WorkspaceSidebar mobileOpen={false} onMobileClose={() => undefined} />
      </MemoryRouter>,
    );

    const activeGroup = await screen.findByRole("button", {
      name: "收起希沃组织主库任务列表",
    });
    expect(activeGroup).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: /回滚教师电话/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /全校组织同步/ })).toBeInTheDocument();
    expect(screen.getByRole("button", {
      name: "展开另一希沃目标任务列表",
    })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("link", { name: /临时学生同步/ })).not.toBeInTheDocument();
  });

  it("keeps a source group collapsed when history refreshes", async () => {
    const user = userEvent.setup();
    const history = vi.spyOn(agentApi, "history")
      .mockResolvedValueOnce({
        items: [
          historyItem("sync-task", "全校组织同步", "sync", "source-a", "希沃组织主库"),
        ],
        next_cursor: null,
      })
      .mockResolvedValueOnce({
        items: [
          historyItem("sync-task", "全校组织同步", "sync", "source-a", "希沃组织主库"),
          historyItem("rollback-task", "回滚教师电话", "rollback", "source-a", "希沃组织主库"),
        ],
        next_cursor: null,
      });

    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <WorkspaceSidebar mobileOpen={false} onMobileClose={() => undefined} />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("button", {
      name: "收起希沃组织主库任务列表",
    }));
    expect(screen.getByRole("button", {
      name: "展开希沃组织主库任务列表",
    })).toHaveAttribute("aria-expanded", "false");

    act(() => window.dispatchEvent(new Event(TASK_HISTORY_UPDATED_EVENT)));
    await waitFor(() => expect(history).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("2 个任务")).toBeInTheDocument();

    expect(screen.getByRole("button", {
      name: "展开希沃组织主库任务列表",
    })).toHaveAttribute("aria-expanded", "false");
  });
});

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ingestionApi } from "../../api/ingestion";
import type { AgentManualTaskApi } from "../../api/agent";
import { TaskCreatePage } from "./TaskCreatePage";

const csv = "entity_type,id,name\n教师,T01,张三\n学生,S01,李四\n";

function LocationProbe() {
  return <output>{useLocation().pathname}</output>;
}

function renderPage(api?: AgentManualTaskApi) {
  return render(
    <MemoryRouter initialEntries={["/tasks/new"]}>
      <Routes>
        <Route path="/tasks/new" element={<TaskCreatePage api={api} />} />
        <Route path="/tasks/:taskId" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("manual Agent data sync", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.spyOn(ingestionApi, "upload").mockResolvedValue({
      id: "upload-1",
      source_role: "authoritative",
      original_name: "data.csv",
      size_bytes: csv.length,
      detected_encoding: "utf-8",
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("keeps authority upload but requires a writable original CSV target", async () => {
    const user = userEvent.setup();
    const localSources = vi.fn().mockResolvedValue([
      {
        source_ref: "third-party/authority.csv",
        kind: "csv" as const,
        writable_as_target: false,
      },
      {
        source_ref: "seewo/current.csv",
        kind: "csv" as const,
        writable_as_target: true,
      },
    ]);
    renderPage({ startManualTask: vi.fn(), localSources });
    await user.click(screen.getByRole("button", { name: "手动同步" }));

    expect(screen.getByLabelText("选择三方系统 CSV")).toBeInTheDocument();
    const targetKind = screen.getByLabelText("希沃魔方连接方式");
    expect(targetKind).toHaveValue("local");
    expect(
      within(targetKind).queryByRole("option", { name: "上传 CSV 副本" }),
    ).not.toBeInTheDocument();
    expect(within(targetKind).queryByRole("option", { name: "API 连接" })).not.toBeInTheDocument();
    expect(within(targetKind).queryByRole("option", { name: "数据库连接" })).not.toBeInTheDocument();
    expect(await screen.findByLabelText("希沃魔方本地 CSV")).toBeInTheDocument();
    expect(screen.queryByLabelText("核对范围")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "全量对账" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "班级" })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "部门" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "学生" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "教师" })).toBeChecked();
  });

  it("uploads authority evidence but sends the writable original target to the Agent task API", async () => {
    const user = userEvent.setup();
    const startManualTask = vi.fn().mockResolvedValue({
      id: "agent-task-1",
      workflow_version: "new-agent-v1",
      phase: "ingest_and_normalize",
      status: "running",
    });
    const localSources = vi.fn().mockResolvedValue([
      {
        source_ref: "seewo/current.csv",
        kind: "csv" as const,
        writable_as_target: true,
      },
    ]);
    renderPage({ startManualTask, localSources });
    await user.click(screen.getByRole("button", { name: "手动同步" }));
    await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File([csv], "third-party.csv", { type: "text/csv" }));
    await user.selectOptions(
      await screen.findByLabelText("希沃魔方本地 CSV"),
      "seewo/current.csv",
    );
    const start = screen.getByRole("button", { name: "开始同步" });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);

    expect(await screen.findByText("/tasks/agent-task-1")).toBeInTheDocument();
    expect(startManualTask).toHaveBeenCalledWith(expect.objectContaining({
      entity_types: ["department", "student", "teacher"],
      source: { kind: "csv", upload_id: "upload-1" },
      target: { kind: "local", source_ref: "seewo/current.csv" },
    }), expect.any(String));
  });

  it("keeps a configured authority connector while requiring a writable local target", async () => {
    const user = userEvent.setup();
    const startManualTask = vi.fn().mockResolvedValue({
      id: "agent-task-2",
      workflow_version: "new-agent-v1",
      phase: "ingest_and_normalize",
      status: "running",
    });
    const localSources = vi.fn().mockResolvedValue([
      {
        source_ref: "seewo/current.csv",
        kind: "csv" as const,
        writable_as_target: true,
      },
    ]);
    renderPage({ startManualTask, localSources });
    await user.click(screen.getByRole("button", { name: "手动同步" }));
    await user.selectOptions(screen.getByLabelText("三方系统连接方式"), "api");
    await user.type(screen.getByLabelText("三方系统配置 ID"), "third-party-api");
    await user.selectOptions(
      await screen.findByLabelText("希沃魔方本地 CSV"),
      "seewo/current.csv",
    );
    await user.click(screen.getByRole("button", { name: "开始同步" }));

    expect(await screen.findByText("/tasks/agent-task-2")).toBeInTheDocument();
    expect(ingestionApi.upload).not.toHaveBeenCalled();
    expect(startManualTask).toHaveBeenCalledWith(expect.objectContaining({
      source: { kind: "api", configuration_id: "third-party-api" },
      target: { kind: "local", source_ref: "seewo/current.csv" },
    }), expect.any(String));
  });

  it("selects authorized local CSV files and requires a writable target", async () => {
    const user = userEvent.setup();
    const startManualTask = vi.fn().mockResolvedValue({
      id: "agent-task-local",
      workflow_version: "agent-graph-v1",
      phase: "ingest_and_normalize",
      status: "running",
    });
    const localSources = vi.fn().mockResolvedValue([
      {
        source_ref: "third-party/authority.csv",
        kind: "csv",
        writable_as_target: false,
      },
      {
        source_ref: "seewo/current.csv",
        kind: "csv",
        writable_as_target: true,
      },
    ]);
    renderPage({ startManualTask, localSources });
    await user.click(screen.getByRole("button", { name: "手动同步" }));

    await user.selectOptions(screen.getByLabelText("三方系统连接方式"), "local");
    await user.selectOptions(
      await screen.findByLabelText("三方系统本地 CSV"),
      "third-party/authority.csv",
    );
    const targetSelect = await screen.findByLabelText("希沃魔方本地 CSV");
    expect(
      within(targetSelect).queryByRole("option", {
        name: "third-party/authority.csv",
      }),
    ).not.toBeInTheDocument();
    await user.selectOptions(targetSelect, "seewo/current.csv");
    await user.click(screen.getByRole("button", { name: "开始同步" }));

    expect(await screen.findByText("/tasks/agent-task-local")).toBeInTheDocument();
    expect(ingestionApi.upload).not.toHaveBeenCalled();
    expect(startManualTask).toHaveBeenCalledWith(
      expect.objectContaining({
        source: {
          kind: "local",
          source_ref: "third-party/authority.csv",
        },
        target: {
          kind: "local",
          source_ref: "seewo/current.csv",
        },
      }),
      expect.any(String),
    );
  });

  it("keeps completed connector selections after a backend rejection", async () => {
    const user = userEvent.setup();
    const startManualTask = vi.fn().mockRejectedValue(new Error("学校已有活动任务"));
    const localSources = vi.fn().mockResolvedValue([
      {
        source_ref: "seewo/current.csv",
        kind: "csv" as const,
        writable_as_target: true,
      },
    ]);
    renderPage({ startManualTask, localSources });
    await user.click(screen.getByRole("button", { name: "手动同步" }));
    await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File([csv], "third-party.csv", { type: "text/csv" }));
    await user.selectOptions(
      await screen.findByLabelText("希沃魔方本地 CSV"),
      "seewo/current.csv",
    );
    const start = screen.getByRole("button", { name: "开始同步" });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);

    expect(await screen.findByText("学校已有活动任务")).toBeInTheDocument();
    expect(screen.getByText("third-party.csv")).toBeInTheDocument();
    expect(screen.getByLabelText("希沃魔方本地 CSV")).toHaveValue("seewo/current.csv");
    expect(start).toBeEnabled();
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";

import { ingestionApi } from "../../api/ingestion";
import { TaskCreatePage } from "./TaskCreatePage";

const csv = "entity_type,id,name\n教师,T01,张三\n学生,S01,李四\n";

function LocationProbe() {
  return <output>{useLocation().pathname}</output>;
}

describe("manual external data sync", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("reveals CSV controls only after manual sync is selected", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <TaskCreatePage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "外部数据同步" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "系统自动同步，暂未开放" })).toBeDisabled();
    expect(screen.queryByLabelText("选择三方系统 CSV")).not.toBeInTheDocument();
    expect(screen.queryByText("任务草案")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "对账要求" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "手动同步" }));

    expect(screen.getByLabelText("选择三方系统 CSV")).toBeInTheDocument();
    expect(screen.getByLabelText("选择希沃魔方 CSV")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始同步" })).toBeDisabled();
  });

  it("creates a task and opens it after manual sync", async () => {
    const user = userEvent.setup();
    vi.spyOn(ingestionApi, "upload").mockResolvedValue({
      id: "upload-id",
      source_role: "authoritative",
      original_name: "data.csv",
      size_bytes: csv.length,
      detected_encoding: "utf-8",
    });
    vi.spyOn(ingestionApi, "createTask").mockResolvedValue({
      id: "task-001",
      tenant_id: "demo-school",
      scope_id: "全校",
      status: "ready",
      stage: "analysis",
      entity_types: ["organization_unit", "class", "teacher", "student"],
      snapshots: {
        authoritative: { accepted: 2, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
        target: { accepted: 2, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
      },
      workflow: {
        stage: "complete",
        status: "succeeded",
        attempt: 1,
        processed: 2,
        total: 2,
        analysis: { total: 2, completed: 2, succeeded: 2, manual_review: 0, failed: 0 },
        error: null,
      },
      error: null,
    });
    render(
      <MemoryRouter initialEntries={["/tasks/new"]}>
        <Routes>
          <Route path="/tasks/new" element={<TaskCreatePage />} />
          <Route path="/tasks/:taskId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "手动同步" }));
    await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File([csv], "third-party.csv", { type: "text/csv" }));
    await user.upload(screen.getByLabelText("选择希沃魔方 CSV"), new File([csv], "mofa.csv", { type: "text/csv" }));
    const startButton = screen.getByRole("button", { name: "开始同步" });
    await waitFor(() => expect(startButton).toBeEnabled());
    await user.click(startButton);

    expect(await screen.findByText("/tasks/task-001")).toBeInTheDocument();
    expect(ingestionApi.createTask).toHaveBeenCalledTimes(1);
  });

  it("requires complete manual sync settings before submission", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><TaskCreatePage /></MemoryRouter>);
    await user.click(screen.getByRole("button", { name: "手动同步" }));

    const startButton = screen.getByRole("button", { name: "开始同步" });
    await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File([csv], "third-party.csv", { type: "text/csv" }));
    await user.upload(screen.getByLabelText("选择希沃魔方 CSV"), new File([csv], "mofa.csv", { type: "text/csv" }));
    await waitFor(() => expect(startButton).toBeEnabled());

    await user.clear(screen.getByRole("textbox", { name: "同步任务名称" }));
    expect(startButton).toBeDisabled();
  });

  it("preserves the completed draft when backend creation fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(ingestionApi, "upload").mockResolvedValue({
      id: "upload-id",
      source_role: "authoritative",
      original_name: "data.csv",
      size_bytes: csv.length,
      detected_encoding: "utf-8",
    });
    vi.spyOn(ingestionApi, "createTask").mockRejectedValue(new Error("后端暂时不可用"));
    render(
      <MemoryRouter>
        <TaskCreatePage />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "手动同步" }));
    await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File([csv], "third-party.csv", { type: "text/csv" }));
    await user.upload(screen.getByLabelText("选择希沃魔方 CSV"), new File([csv], "mofa.csv", { type: "text/csv" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "开始同步" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "开始同步" }));

    expect(await screen.findByText("后端暂时不可用")).toBeInTheDocument();
    expect(screen.getByText("third-party.csv")).toBeInTheDocument();
    expect(screen.getByText("mofa.csv")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始同步" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "开始同步" }));
    await waitFor(() => expect(ingestionApi.createTask).toHaveBeenCalledTimes(2));
    const firstKey = vi.mocked(ingestionApi.createTask).mock.calls[0][1];
    const retryKey = vi.mocked(ingestionApi.createTask).mock.calls[1][1];
    expect(retryKey).toBe(firstKey);
  });

  it("blocks duplicate submission while creation is in progress", async () => {
    const user = userEvent.setup();
    vi.spyOn(ingestionApi, "upload").mockResolvedValue({
      id: "upload-id",
      source_role: "authoritative",
      original_name: "data.csv",
      size_bytes: csv.length,
      detected_encoding: "utf-8",
    });
    vi.spyOn(ingestionApi, "createTask").mockImplementation(() => new Promise(() => undefined));
    render(
      <MemoryRouter>
        <TaskCreatePage />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "手动同步" }));
    await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File([csv], "third-party.csv", { type: "text/csv" }));
    await user.upload(screen.getByLabelText("选择希沃魔方 CSV"), new File([csv], "mofa.csv", { type: "text/csv" }));
    const startButton = screen.getByRole("button", { name: "开始同步" });
    await waitFor(() => expect(startButton).toBeEnabled());
    await user.click(startButton);

    await waitFor(() => expect(ingestionApi.createTask).toHaveBeenCalledTimes(1));
    expect(startButton).toBeDisabled();
    await user.click(startButton);
    expect(ingestionApi.createTask).toHaveBeenCalledTimes(1);
  });
});

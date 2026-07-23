import { render, screen, waitFor } from "@testing-library/react";
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

  it("reuses the existing CSV picker but removes legacy scope and processing controls", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "手动同步" }));

    expect(screen.getByLabelText("选择三方系统 CSV")).toBeInTheDocument();
    expect(screen.getByLabelText("选择希沃魔方 CSV")).toBeInTheDocument();
    expect(screen.queryByLabelText("核对范围")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "全量对账" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "班级" })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "部门" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "学生" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "教师" })).toBeChecked();
  });

  it("submits CSV connectors through the Agent task API", async () => {
    const user = userEvent.setup();
    const startManualTask = vi.fn().mockResolvedValue({
      id: "agent-task-1",
      workflow_version: "new-agent-v1",
      phase: "ingest_and_normalize",
      status: "running",
    });
    renderPage({ startManualTask });
    await user.click(screen.getByRole("button", { name: "手动同步" }));
    await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File([csv], "third-party.csv", { type: "text/csv" }));
    await user.upload(screen.getByLabelText("选择希沃魔方 CSV"), new File([csv], "seewo.csv", { type: "text/csv" }));
    const start = screen.getByRole("button", { name: "开始同步" });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);

    expect(await screen.findByText("/tasks/agent-task-1")).toBeInTheDocument();
    expect(startManualTask).toHaveBeenCalledWith(expect.objectContaining({
      entity_types: ["department", "student", "teacher"],
      source: { kind: "csv", upload_id: "upload-1" },
      target: { kind: "csv", upload_id: "upload-1" },
    }), expect.any(String));
  });

  it("supports configured API and database connectors without pretending to upload them", async () => {
    const user = userEvent.setup();
    const startManualTask = vi.fn().mockResolvedValue({
      id: "agent-task-2",
      workflow_version: "new-agent-v1",
      phase: "ingest_and_normalize",
      status: "running",
    });
    renderPage({ startManualTask });
    await user.click(screen.getByRole("button", { name: "手动同步" }));
    await user.selectOptions(screen.getByLabelText("三方系统连接方式"), "api");
    await user.type(screen.getByLabelText("三方系统配置 ID"), "third-party-api");
    await user.selectOptions(screen.getByLabelText("希沃魔方连接方式"), "database");
    await user.type(screen.getByLabelText("希沃魔方配置 ID"), "seewo-db");
    await user.click(screen.getByRole("button", { name: "开始同步" }));

    expect(await screen.findByText("/tasks/agent-task-2")).toBeInTheDocument();
    expect(ingestionApi.upload).not.toHaveBeenCalled();
    expect(startManualTask).toHaveBeenCalledWith(expect.objectContaining({
      source: { kind: "api", configuration_id: "third-party-api" },
      target: { kind: "database", configuration_id: "seewo-db" },
    }), expect.any(String));
  });

  it("keeps completed connector selections after a backend rejection", async () => {
    const user = userEvent.setup();
    const startManualTask = vi.fn().mockRejectedValue(new Error("学校已有活动任务"));
    renderPage({ startManualTask });
    await user.click(screen.getByRole("button", { name: "手动同步" }));
    await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File([csv], "third-party.csv", { type: "text/csv" }));
    await user.upload(screen.getByLabelText("选择希沃魔方 CSV"), new File([csv], "seewo.csv", { type: "text/csv" }));
    const start = screen.getByRole("button", { name: "开始同步" });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);

    expect(await screen.findByText("学校已有活动任务")).toBeInTheDocument();
    expect(screen.getByText("third-party.csv")).toBeInTheDocument();
    expect(screen.getByText("seewo.csv")).toBeInTheDocument();
    expect(start).toBeEnabled();
  });
});

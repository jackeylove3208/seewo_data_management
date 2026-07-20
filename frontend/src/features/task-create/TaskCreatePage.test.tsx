import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";

import { ingestionApi } from "../../api/ingestion";
import * as csvSummary from "./csvSummary";
import { TaskCreatePage } from "./TaskCreatePage";
import type { CsvSummary } from "./csvSummary";

const csv = "entity_type,id,name\n教师,T01,张三\n学生,S01,李四\n";

function LocationProbe() {
  return <output>{useLocation().pathname}</output>;
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function summary(total: number): CsvSummary {
  return {
    total,
    counts: { organization_unit: 0, class: 0, teacher: total, student: 0 },
    sample: [],
  };
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

  it("ignores a stale source summary after a newer file is selected", async () => {
    const user = userEvent.setup();
    const stale = deferred<CsvSummary>();
    const latest = deferred<CsvSummary>();
    vi.spyOn(csvSummary, "summarizeCsv")
      .mockImplementationOnce(() => stale.promise)
      .mockImplementationOnce(() => latest.promise);
    render(<MemoryRouter><TaskCreatePage /></MemoryRouter>);

    await user.click(screen.getByRole("button", { name: "手动同步" }));
    const sourceInput = screen.getByLabelText("选择三方系统 CSV");
    await user.upload(sourceInput, new File([csv], "stale.csv", { type: "text/csv" }));
    await user.upload(sourceInput, new File([csv], "latest.csv", { type: "text/csv" }));

    await act(async () => {
      latest.resolve(summary(2));
    });
    expect(await screen.findByText("latest.csv")).toBeInTheDocument();
    expect(screen.getByText("2 条数据")).toBeInTheDocument();

    await act(async () => {
      stale.resolve(summary(1));
    });
    await waitFor(() => expect(screen.getByText("latest.csv")).toBeInTheDocument());
    expect(screen.queryByText("stale.csv")).not.toBeInTheDocument();
    expect(screen.getByText("2 条数据")).toBeInTheDocument();
  });

  it("ignores a stale source parse failure after a newer file is selected", async () => {
    const user = userEvent.setup();
    const stale = deferred<CsvSummary>();
    const latest = deferred<CsvSummary>();
    vi.spyOn(csvSummary, "summarizeCsv")
      .mockImplementationOnce(() => stale.promise)
      .mockImplementationOnce(() => latest.promise);
    render(<MemoryRouter><TaskCreatePage /></MemoryRouter>);

    await user.click(screen.getByRole("button", { name: "手动同步" }));
    const sourceInput = screen.getByLabelText("选择三方系统 CSV");
    await user.upload(sourceInput, new File([csv], "stale.csv", { type: "text/csv" }));
    await user.upload(sourceInput, new File([csv], "latest.csv", { type: "text/csv" }));

    await act(async () => {
      latest.resolve(summary(2));
    });
    expect(await screen.findByText("latest.csv")).toBeInTheDocument();
    expect(screen.getByText("2 条数据")).toBeInTheDocument();

    await act(async () => {
      stale.reject(new Error("旧文件解析失败"));
    });
    await waitFor(() => expect(screen.getByText("latest.csv")).toBeInTheDocument());
    expect(screen.queryByText("旧文件解析失败")).not.toBeInTheDocument();
    expect(screen.getByText("2 条数据")).toBeInTheDocument();
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

    await user.type(screen.getByRole("textbox", { name: "同步任务名称" }), "新同步任务");
    await user.clear(screen.getByRole("textbox", { name: "核对范围" }));
    expect(startButton).toBeDisabled();

    await user.type(screen.getByRole("textbox", { name: "核对范围" }), "七年级");
    await user.click(screen.getByRole("button", { name: "清空选择" }));
    expect(startButton).toBeDisabled();

    await user.click(screen.getByRole("checkbox", { name: "教师" }));
    expect(startButton).toBeEnabled();
  });

  it("preserves a valid target when the current source file cannot be parsed", async () => {
    const user = userEvent.setup();
    const originalSummarizeCsv = csvSummary.summarizeCsv;
    vi.spyOn(csvSummary, "summarizeCsv").mockImplementation((file) => (
      file.name === "broken.csv"
        ? Promise.reject(new Error("CSV 文件无法解析，请检查格式"))
        : originalSummarizeCsv(file)
    ));
    render(<MemoryRouter><TaskCreatePage /></MemoryRouter>);

    await user.click(screen.getByRole("button", { name: "手动同步" }));
    await user.upload(screen.getByLabelText("选择希沃魔方 CSV"), new File([csv], "valid-target.csv", { type: "text/csv" }));
    expect(await screen.findByText("2 条数据")).toBeInTheDocument();
    await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File(["broken"], "broken.csv", { type: "text/csv" }));

    expect(await screen.findByText("CSV 文件无法解析，请检查格式")).toBeInTheDocument();
    expect(screen.getByText("valid-target.csv")).toBeInTheDocument();
    expect(screen.getByText("2 条数据")).toBeInTheDocument();
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

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";

import { ingestionApi } from "../../api/ingestion";
import { TaskCreatePage } from "./TaskCreatePage";

const csv = "entity_type,id,name\n教师,T01,张三\n学生,S01,李四\n";

describe("conversational task creation", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("keeps explicit creation disabled until both demo files are ready", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <TaskCreatePage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "和 AI 一起新建对账" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建对账" })).toBeDisabled();

    await user.upload(
      screen.getByLabelText("选择三方系统 CSV"),
      new File([csv], "third-party.csv", { type: "text/csv" }),
    );
    expect(screen.getByRole("button", { name: "创建对账" })).toBeDisabled();
    await user.upload(
      screen.getByLabelText("选择希沃魔方 CSV"),
      new File([csv], "mofa.csv", { type: "text/csv" }),
    );

    await waitFor(() => expect(screen.getByRole("button", { name: "创建对账" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "清空选择" }));
    expect(screen.getByRole("button", { name: "创建对账" })).toBeDisabled();

    await user.click(screen.getByRole("checkbox", { name: "教师" }));
    expect(screen.getByRole("button", { name: "创建对账" })).toBeEnabled();
  });

  it("updates the task draft from a conversational request", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <TaskCreatePage />
      </MemoryRouter>,
    );

    await user.type(screen.getByRole("textbox", { name: "对账要求" }), "只核对七年级的老师和学生");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByDisplayValue("七年级")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "教师" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "学生" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "班级" })).not.toBeChecked();
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

    await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File([csv], "third-party.csv", { type: "text/csv" }));
    await user.upload(screen.getByLabelText("选择希沃魔方 CSV"), new File([csv], "mofa.csv", { type: "text/csv" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "创建对账" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "创建对账" }));

    expect(await screen.findByText("后端暂时不可用")).toBeInTheDocument();
    expect(screen.getByText("third-party.csv")).toBeInTheDocument();
    expect(screen.getByText("mofa.csv")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建对账" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "创建对账" }));
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

    await user.upload(screen.getByLabelText("选择三方系统 CSV"), new File([csv], "third-party.csv", { type: "text/csv" }));
    await user.upload(screen.getByLabelText("选择希沃魔方 CSV"), new File([csv], "mofa.csv", { type: "text/csv" }));
    const createButton = screen.getByRole("button", { name: "创建对账" });
    await waitFor(() => expect(createButton).toBeEnabled());
    await user.click(createButton);

    await waitFor(() => expect(ingestionApi.createTask).toHaveBeenCalledTimes(1));
    expect(createButton).toBeDisabled();
    await user.click(createButton);
    expect(ingestionApi.createTask).toHaveBeenCalledTimes(1);
  });
});

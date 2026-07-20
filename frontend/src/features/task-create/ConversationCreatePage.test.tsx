import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { TaskCreationAssistant } from "./types";
import { TASK_INTENT_STORAGE_KEY } from "./draftHandoff";
import { ConversationCreatePage } from "./ConversationCreatePage";

function renderPage(assistant?: TaskCreationAssistant) {
  return render(
    <MemoryRouter initialEntries={["/conversations/new"]}>
      <Routes>
        <Route path="/conversations/new" element={<ConversationCreatePage assistant={assistant} />} />
        <Route path="/tasks/new" element={<div>外部数据同步目的页</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe("independent task conversation", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  it("shows conversation and an independent editable draft without CSV controls", () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "新建对话" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "任务草案" })).toBeInTheDocument();
    expect(screen.getByLabelText("任务名称")).toHaveValue("");
    expect(screen.getByLabelText("核对范围")).toHaveValue("");
    expect(screen.getByRole("button", { name: "继续外部数据同步" })).toBeDisabled();
    expect(screen.queryByLabelText("选择三方系统 CSV")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("选择希沃魔方 CSV")).not.toBeInTheDocument();
  });

  it("updates recognized task information through the assistant", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("对账目标"), "只核对七年级的老师和学生");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByDisplayValue("七年级教师、学生核对")).toBeInTheDocument();
    expect(screen.getByLabelText("核对范围")).toHaveValue("七年级");
    expect(screen.getByLabelText("教师")).toBeChecked();
    expect(screen.getByLabelText("学生")).toBeChecked();
    expect(screen.getByLabelText("部门")).not.toBeChecked();
  });

  it("requires complete editable task information before handoff", async () => {
    const user = userEvent.setup();
    renderPage();
    const handoff = screen.getByRole("button", { name: "继续外部数据同步" });

    expect(handoff).toBeDisabled();
    await user.type(screen.getByLabelText("核对范围"), "全校");
    await user.click(screen.getByLabelText("教师"));
    await user.clear(screen.getByLabelText("任务名称"));
    expect(handoff).toBeDisabled();
    await user.type(screen.getByLabelText("任务名称"), "自定义教师核对");
    expect(handoff).toBeEnabled();
  });

  it("persists the latest draft and navigates to external data sync", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.clear(screen.getByLabelText("任务名称"));
    await user.type(screen.getByLabelText("任务名称"), "自定义教师核对");
    await user.type(screen.getByLabelText("核对范围"), "七年级");
    await user.click(screen.getByLabelText("教师"));
    await user.click(screen.getByRole("button", { name: "继续外部数据同步" }));

    expect(await screen.findByText("外部数据同步目的页")).toBeInTheDocument();
    expect(JSON.parse(sessionStorage.getItem(TASK_INTENT_STORAGE_KEY) ?? "{}").draft.title).toBe("自定义教师核对");
  });

  it("preserves the draft when the assistant fails", async () => {
    const failingAssistant: TaskCreationAssistant = {
      respond: () => Promise.reject(new Error("assistant unavailable")),
    };
    const user = userEvent.setup();
    renderPage(failingAssistant);

    await user.clear(screen.getByLabelText("任务名称"));
    await user.type(screen.getByLabelText("任务名称"), "保留的草案");
    await user.type(screen.getByLabelText("对账目标"), "核对高中部教师");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("没有理解这条要求，请换一种说法或直接编辑任务草案。")).toBeInTheDocument();
    expect(screen.getByLabelText("任务名称")).toHaveValue("保留的草案");
  });

  it("rejects malformed assistant output without mutating the draft", async () => {
    const malformedAssistant: TaskCreationAssistant = {
      respond: () => Promise.resolve({
        kind: "normal",
        message: "无效响应",
        patch: { entityTypes: ["unsupported"] },
      } as unknown as Awaited<ReturnType<TaskCreationAssistant["respond"]>>),
    };
    const user = userEvent.setup();
    renderPage(malformedAssistant);

    await user.type(screen.getByLabelText("任务名称"), "保留的草案");
    await user.type(screen.getByLabelText("对账目标"), "核对高中部教师");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("没有理解这条要求，请换一种说法或直接编辑任务草案。")).toBeInTheDocument();
    expect(screen.getByLabelText("任务名称")).toHaveValue("保留的草案");
    expect(screen.getByLabelText("教师")).not.toBeChecked();
  });

  it("locks direct draft edits while an assistant response is pending", async () => {
    const pending = deferred<Awaited<ReturnType<TaskCreationAssistant["respond"]>>>();
    const assistant: TaskCreationAssistant = { respond: () => pending.promise };
    const user = userEvent.setup();
    renderPage(assistant);

    await user.type(screen.getByLabelText("对账目标"), "核对高中部教师");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(screen.getByLabelText("任务名称")).toBeDisabled();
    expect(screen.getByLabelText("核对范围")).toBeDisabled();
    expect(screen.getByRole("button", { name: "指定范围" })).toBeDisabled();
    expect(screen.getByLabelText("教师")).toBeDisabled();
    expect(screen.getByRole("button", { name: "清空选择" })).toBeDisabled();
    await act(async () => pending.resolve({
      kind: "normal",
      message: "草案已更新",
      patch: { title: "高中部教师核对", scopeLabel: "高中部", entityTypes: ["teacher"], snapshotMode: "partial" },
    }));
    expect(await screen.findByDisplayValue("高中部教师核对")).toBeEnabled();
  });

  it("clears a stale handoff when a fresh conversation starts without mutating history", async () => {
    sessionStorage.setItem(TASK_INTENT_STORAGE_KEY, JSON.stringify({ version: 1, draft: {
      title: "旧草案",
      scopeLabel: "高中部",
      snapshotMode: "partial",
      entityTypes: ["teacher"],
    } }));
    localStorage.setItem("mofa-reconciliation-tasks", JSON.stringify([{ id: "history-1" }]));

    renderPage();

    await waitFor(() => expect(sessionStorage.getItem(TASK_INTENT_STORAGE_KEY)).toBeNull());
    expect(localStorage.getItem("mofa-reconciliation-tasks")).toBe(JSON.stringify([{ id: "history-1" }]));
  });
});

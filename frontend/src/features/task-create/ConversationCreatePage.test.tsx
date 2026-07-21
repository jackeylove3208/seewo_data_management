import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import type { AssistantRequest, TaskCreationAssistant } from "./types";
import { TASK_INTENT_STORAGE_KEY } from "./draftHandoff";
import { ConversationCreatePage } from "./ConversationCreatePage";

function renderPage(assistant?: TaskCreationAssistant) {
  return render(
    <MemoryRouter>
      <ConversationCreatePage assistant={assistant} />
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

describe("independent agent conversation", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  it("shows only the agent conversation without task configuration controls", () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "新建对话" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "新建对话" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "任务草案" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("任务名称")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("核对范围")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "继续外部数据同步" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("选择三方系统 CSV")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("选择希沃魔方 CSV")).not.toBeInTheDocument();
  });

  it("responds to a synchronization request without exposing a task draft", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("对账目标"), "只核对七年级的老师和学生");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("已记录七年级的教师、学生同步需求。")).toBeInTheDocument();
    expect(screen.queryByText("任务草案", { exact: true })).not.toBeInTheDocument();
    expect(sessionStorage.getItem(TASK_INTENT_STORAGE_KEY)).toBeNull();
  });

  it("keeps recognized intent internally across conversation turns", async () => {
    const requests: AssistantRequest[] = [];
    const assistant: TaskCreationAssistant = {
      respond: (request) => {
        requests.push({
          ...request,
          draft: { ...request.draft, entityTypes: [...request.draft.entityTypes] },
        });
        return Promise.resolve(requests.length === 1
          ? { kind: "normal", message: "已识别七年级", patch: { scopeLabel: "七年级" } }
          : {
            kind: "normal",
            message: "已识别教师范围",
            patch: { title: "七年级教师同步", entityTypes: ["teacher"], snapshotMode: "partial" },
          });
      },
    };
    const user = userEvent.setup();
    renderPage(assistant);

    await user.type(screen.getByLabelText("对账目标"), "先看七年级");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("已识别七年级")).toBeInTheDocument();
    await user.type(screen.getByLabelText("对账目标"), "只同步教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("已识别教师范围")).toBeInTheDocument();

    expect(requests).toHaveLength(2);
    expect(requests[1].draft.scopeLabel).toBe("七年级");
    expect(requests[1].draft.entityTypes).toEqual([]);
  });

  it("recovers from an assistant failure without exposing manual editing", async () => {
    const failingAssistant: TaskCreationAssistant = {
      respond: () => Promise.reject(new Error("assistant unavailable")),
    };
    const user = userEvent.setup();
    renderPage(failingAssistant);

    await user.type(screen.getByLabelText("对账目标"), "核对高中部教师");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("没有理解这条要求，请换一种说法后重试。")).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeEnabled();
    expect(screen.queryByLabelText("任务名称")).not.toBeInTheDocument();
  });

  it("rejects malformed assistant output and keeps the conversation usable", async () => {
    const malformedAssistant: TaskCreationAssistant = {
      respond: () => Promise.resolve({
        kind: "normal",
        message: "无效响应",
        patch: { entityTypes: ["unsupported"] },
      } as unknown as Awaited<ReturnType<TaskCreationAssistant["respond"]>>),
    };
    const user = userEvent.setup();
    renderPage(malformedAssistant);

    await user.type(screen.getByLabelText("对账目标"), "核对高中部教师");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("没有理解这条要求，请换一种说法后重试。")).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeEnabled();
    expect(screen.queryByText("无效响应")).not.toBeInTheDocument();
  });

  it("locks the composer while an assistant response is pending", async () => {
    const pending = deferred<Awaited<ReturnType<TaskCreationAssistant["respond"]>>>();
    const assistant: TaskCreationAssistant = { respond: () => pending.promise };
    const user = userEvent.setup();
    renderPage(assistant);

    const composer = screen.getByLabelText("对账目标");
    await user.type(composer, "核对高中部教师");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(composer).toBeDisabled();
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    expect(screen.getByText("正在理解同步需求")).toBeInTheDocument();
    await act(async () => pending.resolve({
      kind: "normal",
      message: "已记录高中部教师同步需求。",
      patch: { title: "高中部教师同步", scopeLabel: "高中部", entityTypes: ["teacher"], snapshotMode: "partial" },
    }));
    expect(await screen.findByText("已记录高中部教师同步需求。")).toBeInTheDocument();
    expect(composer).toBeEnabled();
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

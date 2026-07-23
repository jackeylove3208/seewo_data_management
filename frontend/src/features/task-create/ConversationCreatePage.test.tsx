import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentConversationApi } from "../../api/agent";
import { ConversationCreatePage } from "./ConversationCreatePage";

function api(overrides: Partial<AgentConversationApi> = {}): AgentConversationApi {
  return {
    createConversation: vi.fn().mockResolvedValue({ id: "conversation-1", status: "active" }),
    sendMessage: vi.fn().mockResolvedValue({
      message: "已整理好全校教师同步需求。",
      intent: { title: "全校教师同步", entity_types: ["teacher"] },
      start_confirmation: {
        title: "全校教师同步",
        summary: "将同步三方系统与希沃魔方的教师数据",
        entity_types: ["teacher"],
      },
    }),
    startTask: vi.fn().mockResolvedValue({
      id: "task-1",
      workflow_version: "new-agent-v1",
      phase: "ingest_and_normalize",
      status: "running",
    }),
    events: vi.fn().mockResolvedValue({ cursor: "cursor-1", events: [] }),
    terminate: vi.fn().mockResolvedValue({ status: "terminating" }),
    ...overrides,
  };
}

describe("backend Agent conversation", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  it("shows only the Agent chat without a browser-owned task draft", () => {
    render(<ConversationCreatePage agentApi={api({
      createConversation: vi.fn().mockReturnValue(new Promise(() => undefined)),
    })} />);

    expect(screen.getByRole("heading", { name: "新建对话" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "新建对话" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "任务草案" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("任务名称")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("选择三方系统 CSV")).not.toBeInTheDocument();
  });

  it("shows backend confirmation and locks ordinary input after task start", async () => {
    const backend = api();
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));

    expect(await screen.findByText(/数据接入/)).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeDisabled();
    expect(screen.getByRole("button", { name: "终止任务" })).toBeInTheDocument();
    expect(backend.startTask).toHaveBeenCalledWith(
      "conversation-1",
      expect.objectContaining({ title: "全校教师同步" }),
      expect.any(String),
    );
  });

  it("renders grouped approval and masked conflict evidence from persisted events", async () => {
    const backend = api({
      startTask: vi.fn().mockResolvedValue({
        id: "task-2",
        workflow_version: "new-agent-v1",
        phase: "analyze_batches",
        status: "running",
      }),
      events: vi.fn().mockResolvedValue({
        cursor: "cursor-2",
        events: [
          { id: "approval-1", cursor: "1", type: "approval_required", payload: { group_id: "group-1" }, created_at: "" },
          { id: "conflict-1", cursor: "2", type: "clarification_required", payload: { masked_evidence: "手机号尾号 ****" }, created_at: "" },
        ],
      }),
      approveGroup: vi.fn().mockResolvedValue({}),
    });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await user.type(screen.getByLabelText("对账目标"), "同步学生");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));

    expect(await screen.findByText(/手机号尾号/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "同意本组" }));
    expect(backend.approveGroup).toHaveBeenCalledWith("task-2", "group-1");
  });

  it("keeps the backend intent available after a lock conflict", async () => {
    const backend = api({ startTask: vi.fn().mockRejectedValue(new Error("lock conflict")) });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));

    expect(await screen.findByText("任务启动失败，现有需求仍然保留，可以重试。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认开始同步" })).toBeInTheDocument();
  });
});

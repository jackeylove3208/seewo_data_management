import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentConversationApi } from "../../api/agent";
import { ConversationCreatePage } from "./ConversationCreatePage";

function api(overrides: Partial<AgentConversationApi> = {}): AgentConversationApi {
  return {
    currentConversation: vi.fn().mockResolvedValue(null),
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
    task: vi.fn().mockResolvedValue(undefined),
    terminate: vi.fn().mockResolvedValue({ status: "terminating" }),
    ...overrides,
  };
}

async function waitForComposer() {
  await waitFor(() => expect(screen.getByLabelText("对账目标")).toBeEnabled());
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

  it("keeps the composer disabled until conversation recovery finishes", () => {
    const currentConversation = vi.fn().mockReturnValue(new Promise(() => undefined));
    render(<ConversationCreatePage agentApi={api({ currentConversation })} />);

    expect(screen.getByLabelText("对账目标")).toBeDisabled();
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  });

  it("shows backend confirmation and locks ordinary input after task start", async () => {
    const backend = api();
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
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

    await waitForComposer();
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

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));

    expect(await screen.findByText("任务启动失败，现有需求仍然保留，可以重试。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认开始同步" })).toBeInTheDocument();
  });

  it("restores backend messages and active task after the page remounts", async () => {
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-restored",
        status: "active",
        messages: [
          { id: "message-1", role: "user", kind: "normal", text: "同步全校学生", created_at: "" },
          { id: "message-2", role: "assistant", kind: "normal", text: "任务已经开始。", created_at: "" },
        ],
        intent: { title: "全校学生同步", entity_types: ["student"] },
        task: {
          id: "task-restored",
          workflow_version: "agent-graph-v1",
          phase: "analyze_batches",
          status: "running",
        },
      }),
    } as Partial<AgentConversationApi>);

    const first = render(<ConversationCreatePage agentApi={backend} />);
    expect(await screen.findByText("同步全校学生")).toBeInTheDocument();
    expect(screen.getByText("任务已经开始。")).toBeInTheDocument();
    expect(screen.getByText(/Agent 分析/)).toBeInTheDocument();
    first.unmount();

    render(<ConversationCreatePage agentApi={backend} />);
    expect(await screen.findByText("同步全校学生")).toBeInTheDocument();
    expect(backend.createConversation).not.toHaveBeenCalled();
  });

  it("restores an unstarted backend confirmation after navigation", async () => {
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-confirmation",
        status: "active",
        messages: [
          { id: "message-1", role: "user", kind: "normal", text: "同步全校学生", created_at: "" },
          { id: "message-2", role: "assistant", kind: "normal", text: "已确认同步需求。", created_at: "" },
        ],
        intent: { title: "全校学生同步", entity_types: ["student"] },
        start_confirmation: {
          title: "全校学生同步",
          summary: "已确认同步需求。",
          entity_types: ["student"],
        },
        task: null,
      }),
    } as Partial<AgentConversationApi>);

    render(<ConversationCreatePage agentApi={backend} />);

    expect(await screen.findByRole("button", { name: "确认开始同步" })).toBeInTheDocument();
    expect(screen.getAllByText("已确认同步需求。")).toHaveLength(2);
  });

  it("renders exhausted model retries as a blocked Chinese timeline", async () => {
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-blocked",
        status: "active",
        messages: [
          { id: "message-1", role: "user", kind: "normal", text: "同步学生数据", created_at: "" },
        ],
        task: {
          id: "task-blocked",
          workflow_version: "new-agent-v1",
          phase: "analyze_batches",
          status: "blocked_model_error",
        },
      }),
      events: vi.fn().mockResolvedValue({
        cursor: "2",
        events: [
          {
            id: "event-1",
            cursor: "1",
            type: "model_attempt_failed",
            phase: "analyze_batches",
            payload: { attempt: 4, attempt_count: 4, failure_category: "model_timeout" },
            created_at: "2026-07-24T03:10:00Z",
          },
          {
            id: "event-2",
            cursor: "2",
            type: "model_retry_exhausted",
            phase: "analyze_batches",
            status: "blocked_model_error",
            payload: { attempt_count: 4 },
            created_at: "2026-07-24T03:10:01Z",
          },
        ],
      }),
    } as Partial<AgentConversationApi>);

    render(<ConversationCreatePage agentApi={backend} />);

    expect(await screen.findByText("模型响应超时")).toBeInTheDocument();
    expect(screen.getByText("模型分析已暂停")).toBeInTheDocument();
    expect(screen.queryByText("model_retry_exhausted")).not.toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Agent 任务进度" })).toHaveClass("blocked");
    expect(screen.getByRole("button", { name: "终止任务" })).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeDisabled();
  });

  it("confirms controlled graph termination through a persisted gate", async () => {
    const previewTermination = vi.fn().mockResolvedValue({
      id: "termination-gate-1",
      kind: "termination_confirmation",
      status: "pending",
      item_count: 1,
    });
    const decideGraphGate = vi.fn().mockResolvedValue({
      gate_id: "termination-gate-1",
      status: "approved",
      graph_cursor: 3,
    });
    const terminate = vi.fn();
    const backend = api({
      startTask: vi.fn().mockResolvedValue({
        id: "task-graph",
        workflow_version: "agent-graph-v1",
        phase: "ingest_and_normalize",
        status: "running",
      }),
      previewTermination,
      decideGraphGate,
      terminate,
    } as Partial<AgentConversationApi>);
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));
    await user.click(await screen.findByRole("button", { name: "终止任务" }));

    expect(await screen.findByRole("dialog", { name: "确认终止当前任务？" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认终止" }));

    expect(previewTermination).toHaveBeenCalledWith("task-graph");
    expect(decideGraphGate).toHaveBeenCalledWith(
      "task-graph",
      "termination-gate-1",
      "approve",
      "操作人确认终止当前任务",
    );
    expect(terminate).not.toHaveBeenCalled();
  });

  it("unlocks a new conversation after polling a terminated task", async () => {
    const task = vi.fn().mockResolvedValue({
      id: "task-terminated",
      workflow_version: "agent-graph-v1",
      phase: "terminal",
      status: "terminated",
    });
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-terminated",
        status: "active",
        messages: [
          { id: "message-1", role: "user", kind: "normal", text: "同步学生", created_at: "" },
        ],
        task: {
          id: "task-terminated",
          workflow_version: "agent-graph-v1",
          phase: "generate_report",
          status: "running",
        },
      }),
      task,
    });

    render(<ConversationCreatePage agentApi={backend} />);

    await waitFor(() => expect(task).toHaveBeenCalledWith("task-terminated"));
    await waitFor(() => expect(screen.getByLabelText("对账目标")).toBeEnabled());
    expect(screen.queryByRole("button", { name: "终止任务" })).not.toBeInTheDocument();
  });

  it("keeps direct termination for legacy Agent tasks", async () => {
    const terminate = vi.fn().mockResolvedValue({ status: "terminated" });
    const backend = api({ terminate });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));
    await user.click(await screen.findByRole("button", { name: "终止任务" }));

    expect(terminate).toHaveBeenCalledWith("task-1");
  });

  it("shows the backend conversation error and keeps input retryable", async () => {
    const backend = api({
      sendMessage: vi.fn().mockRejectedValue(
        new Error("对话模型暂时无法生成有效回复，请稍后重试。"),
      ),
    });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "你是谁");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(
      await screen.findByText("对话模型暂时无法生成有效回复，请稍后重试。"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeEnabled();
  });
});

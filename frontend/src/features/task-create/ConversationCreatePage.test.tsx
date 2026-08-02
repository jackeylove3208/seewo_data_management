import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AgentConnectorSelection,
  AgentConversationApi,
  AgentGraphProgress,
} from "../../api/agent";
import { ApiError } from "../../api/client";
import { TASK_HISTORY_UPDATED_EVENT } from "../../data/taskHistory";
import { ConversationCreatePage } from "./ConversationCreatePage";

function api(overrides: Partial<AgentConversationApi> = {}): AgentConversationApi {
  return {
    currentConversation: vi.fn().mockResolvedValue(null),
    createConversation: vi.fn().mockResolvedValue({ id: "conversation-1", status: "active" }),
    resetConversation: vi.fn().mockResolvedValue({
      id: "conversation-reset",
      status: "active",
    }),
    sendMessage: vi.fn().mockResolvedValue({
      accepted_message: "同步全校教师",
      message: "已整理好全校教师同步需求。",
      intent: {
        title: "全校教师同步",
        entity_types: ["teacher"],
        source: {
          kind: "local",
          source_ref: "third-party/teacher-roster.csv",
        },
        target: {
          kind: "local",
          source_ref: "seewo/teacher-roster.csv",
        },
      },
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
    submitClarificationSelection: vi.fn().mockResolvedValue({
      decision_id: "clarification-1",
      status: "interpreted",
      task_id: "task-identity",
      decision: "select_candidate",
      selected_candidate_id: "candidate-1",
      interpretation_zh: "你选择了第三方候选 A，确认后继续。",
      requires_second_confirmation: true,
    }),
    confirmClarification: vi.fn().mockResolvedValue({ status: "confirmed" }),
    ...overrides,
  };
}

async function waitForComposer() {
  await waitFor(() => expect(screen.getByLabelText("对账目标")).toBeEnabled());
}

function identityGraph(
  status: "pending" | "interpreted" = "pending",
  interpretation?: string,
): AgentGraphProgress {
  return {
    task_id: "task-identity",
    workflow_version: "agent-graph-v1",
    graph_version: "agent-controlled-graph-v1",
    graph_cursor: 6,
    current_node: "resolve_identity_conflicts",
    business_stage: "agent_analysis",
    current_action_zh: "正在等待身份冲突说明",
    status: "waiting_human",
    can_terminate: true,
    termination_requested: false,
    human_gates: [{
      id: "identity-gate-1",
      kind: "identity_conflict",
      status: "pending",
      item_count: 1,
      cursor: 5,
      actionable: true,
      conflicts: [{
        clarification_id: "clarification-1",
        status,
        summary_zh: "唯一身份字段命中了多个第三方权威候选，Agent 无法安全选择。",
        subject: {
          entity_kind: "student",
          category: "student",
          name: "测试学生",
          number: "S-009",
          class_name: "一年级一班",
          phone_masked: "***0009",
          email_masked: "s***@example.test",
        },
        candidates: [
          {
            candidate_id: "candidate-1",
            entity_kind: "student",
            category: "student",
            name: "测试学生",
            number: "S-001",
            class_name: "一年级一班",
            phone_masked: "***0001",
            email_masked: "s***@example.test",
          },
          {
            candidate_id: "candidate-2",
            entity_kind: "student",
            category: "student",
            name: "测试学生二号",
            number: "S-002",
            class_name: "一年级二班",
            phone_masked: "***0002",
            email_masked: "s***@example.test",
          },
        ],
        allowed_outcomes: ["use_candidate", "target_extra"],
        interpretation_zh: interpretation ?? null,
        operator_submission: status === "interpreted"
          ? {
              decision: "select_candidate",
              selected_candidate_id: "candidate-1",
              note: null,
              interpretation_zh:
                interpretation ?? "你选择了第三方候选 A，确认后继续。",
              submitted_at: "2026-07-28T10:00:00Z",
              source: "structured_selection",
            }
          : null,
      }],
    }],
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

    expect(screen.getByRole("heading", { name: "数据同步助手" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "新建对话" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "新建对话" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开启新对话" })).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "任务处理状态" }),
    ).toHaveClass("is-idle");
    expect(screen.getByText("等待创建任务")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "新建对话" }).parentElement)
      .toHaveClass("has-task-status");
    expect(screen.queryByRole("region", { name: "任务草案" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("任务名称")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("选择三方系统 CSV")).not.toBeInTheDocument();
  });

  it("permanently replaces chat after explicit new-conversation confirmation", async () => {
    const resetConversation = vi.fn().mockResolvedValue({
      id: "conversation-reset",
      status: "active",
    });
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-old",
        status: "active",
        messages: [
          {
            id: "old-message",
            role: "user",
            kind: "normal",
            text: "这段旧聊天会被删除",
            created_at: "",
          },
        ],
        task: null,
      }),
      resetConversation,
    });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    expect(await screen.findByText("这段旧聊天会被删除")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "开启新对话" }));

    expect(screen.getByRole("dialog", { name: "开启新对话？" })).toBeInTheDocument();
    expect(screen.getByText(/数据同步任务、治理记录和报告不会被删除/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "永久删除并开启" }));

    await waitFor(() => expect(resetConversation).toHaveBeenCalledWith(expect.any(String)));
    expect(screen.queryByText("这段旧聊天会被删除")).not.toBeInTheDocument();
    expect(
      screen.getByText("你好，我是智能数据同步助手。告诉我希望同步的范围和对象。"),
    ).toBeInTheDocument();
  });

  it("keeps existing chat when new-conversation reset fails", async () => {
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-old",
        status: "active",
        messages: [
          {
            id: "old-message",
            role: "assistant",
            kind: "normal",
            text: "需要保留的旧聊天",
            created_at: "",
          },
        ],
        task: null,
      }),
      resetConversation: vi.fn().mockRejectedValue(
        new ApiError("当前学校仍有任务正在处理", 409, "conversation_active_task"),
      ),
    });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    expect(await screen.findByText("需要保留的旧聊天")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "开启新对话" }));
    await user.click(screen.getByRole("button", { name: "永久删除并开启" }));

    expect(await screen.findByText("当前学校仍有任务正在处理")).toBeInTheDocument();
    expect(screen.getByText("需要保留的旧聊天")).toBeInTheDocument();
  });

  it("keeps the composer disabled until conversation recovery finishes", () => {
    const currentConversation = vi.fn().mockReturnValue(new Promise(() => undefined));
    render(<ConversationCreatePage agentApi={api({ currentConversation })} />);

    expect(screen.getByLabelText("对账目标")).toBeDisabled();
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  });

  it("sends the composer message with Enter", async () => {
    const sendMessage = vi.fn().mockReturnValue(new Promise(() => undefined));
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={api({ sendMessage })} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.keyboard("{Enter}");

    expect(sendMessage).toHaveBeenCalledWith("conversation-1", "同步全校教师");
  });

  it("inserts a newline with Shift+Enter without sending", async () => {
    const sendMessage = vi.fn();
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={api({ sendMessage })} />);

    await waitForComposer();
    const composer = screen.getByLabelText("对账目标");
    await user.type(composer, "第一行");
    await user.keyboard("{Shift>}{Enter}{/Shift}第二行");

    expect(composer).toHaveValue("第一行\n第二行");
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("does not send when Enter confirms an IME composition", async () => {
    const sendMessage = vi.fn();
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={api({ sendMessage })} />);

    await waitForComposer();
    const composer = screen.getByLabelText("对账目标");
    await user.type(composer, "同步教师");
    fireEvent.compositionStart(composer);
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter" });
    fireEvent.compositionEnd(composer);

    expect(sendMessage).not.toHaveBeenCalled();
    expect(composer).toHaveValue("同步教师");
  });

  it("shows an initialization failure outside the assistant conversation", async () => {
    const currentConversation = vi.fn().mockRejectedValue(
      new ApiError("后端服务不可用", 503, "service_unavailable"),
    );
    render(<ConversationCreatePage agentApi={api({ currentConversation })} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("后端服务不可用");
    expect(screen.getByRole("button", { name: "重新连接" })).toBeInTheDocument();
    expect(
      screen.queryByText("对话服务暂时不可用，请稍后重试。"),
    ).not.toBeInTheDocument();
    expect(screen.getAllByRole("article", { name: "同步助手消息" })).toHaveLength(1);
  });

  it("echoes an ordinary question while the assistant reply is pending", async () => {
    const sendMessage = vi.fn().mockReturnValue(new Promise(() => undefined));
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={api({ sendMessage })} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "你是谁");
    await user.click(screen.getByRole("button", { name: "发送" }));

    const userMessage = screen.getByRole("article", { name: "你的消息" });
    expect(within(userMessage).getByText("你是谁")).toBeInTheDocument();
    expect(userMessage).toHaveClass("is-entering");
    fireEvent.animationEnd(userMessage);
    expect(userMessage).not.toHaveClass("is-entering");
    expect(screen.queryByText("消息已提交，正在安全处理。"))
      .not.toBeInTheDocument();
  });

  it("types a new assistant reply before unlocking input and showing confirmation", async () => {
    let resolveReply!: (response: Awaited<ReturnType<AgentConversationApi["sendMessage"]>>) => void;
    const sendMessage = vi.fn().mockReturnValue(new Promise((resolve) => {
      resolveReply = resolve;
    }));
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={api({ sendMessage })} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await act(async () => {
      resolveReply({
        accepted_message: "同步全校教师",
        message: "正在逐字整理同步需求。",
        intent: {
          title: "全校教师同步",
          entity_types: ["teacher"],
          source: { kind: "local", source_ref: "third-party/teacher-roster.csv" },
          target: { kind: "local", source_ref: "seewo/teacher-roster.csv" },
        },
        start_confirmation: {
          title: "全校教师同步",
          summary: "正在逐字整理同步需求。",
          entity_types: ["teacher"],
        },
      });
    });

    await waitFor(() => expect(
      screen.getAllByRole("article", { name: "同步助手消息" }).at(-1),
    ).toHaveAttribute("aria-busy", "true"));
    expect(screen.queryByText("正在逐字整理同步需求。")).not.toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeDisabled();
    expect(screen.queryByLabelText("开始确认")).not.toBeInTheDocument();

    expect(await screen.findByText("正在逐字整理同步需求。", {}, { timeout: 4000 }))
      .toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("对账目标")).toBeEnabled());
    expect(screen.getByLabelText("开始确认")).toBeInTheDocument();
  });

  it("shows restored assistant history immediately without a typewriter replay", async () => {
    render(<ConversationCreatePage agentApi={api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-history",
        status: "active",
        messages: [{
          id: "assistant-history",
          role: "assistant",
          kind: "normal",
          text: "这是已经保存的完整回复。",
          created_at: "",
        }],
        task: null,
      }),
    })} />);

    const restored = (await screen.findByText("这是已经保存的完整回复。"))
      .closest("article");
    expect(restored).toBeInTheDocument();
    expect(restored).not.toHaveAttribute("aria-busy", "true");
  });

  it("shows a new assistant reply immediately when reduced motion is preferred", async () => {
    const originalMatchMedia = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query === "(prefers-reduced-motion: reduce)",
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    let resolveReply!: (response: Awaited<ReturnType<AgentConversationApi["sendMessage"]>>) => void;
    const sendMessage = vi.fn().mockReturnValue(new Promise((resolve) => {
      resolveReply = resolve;
    }));
    const user = userEvent.setup();
    try {
      render(<ConversationCreatePage agentApi={api({ sendMessage })} />);
      await waitForComposer();
      await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
      await user.click(screen.getByRole("button", { name: "发送" }));
      await act(async () => {
        resolveReply({
          accepted_message: "同步全校教师",
          message: "减少动态效果时立即显示。",
          intent: { title: "全校教师同步", entity_types: ["teacher"] },
          start_confirmation: {
            title: "全校教师同步",
            summary: "减少动态效果时立即显示。",
            entity_types: ["teacher"],
          },
        });
      });

      const replyText = screen.getByText("减少动态效果时立即显示。");
      expect(replyText.closest("article")).not.toHaveAttribute("aria-busy", "true");
      expect(screen.getByRole("article", { name: "你的消息" }))
        .not.toHaveClass("is-entering");
      expect(screen.getByLabelText("对账目标")).toBeEnabled();
      expect(screen.getByLabelText("开始确认")).toBeInTheDocument();
    } finally {
      Object.defineProperty(window, "matchMedia", {
        configurable: true,
        value: originalMatchMedia,
      });
    }
  });

  it("configures and tests an API connection without echoing credentials into chat", async () => {
    const configureApiConnection = vi.fn()
      .mockResolvedValueOnce({
        provider_id: "dingtalk",
        state: "invalid",
        required_secret_fields: ["app_key", "app_secret"],
        connection_id: "connection-1",
        display_name: "学校钉钉",
        capabilities: {},
        visibility_summary: {},
        safe_error_code: "connector_permission_denied",
      })
      .mockResolvedValueOnce({
        provider_id: "dingtalk",
        state: "active",
        required_secret_fields: ["app_key", "app_secret"],
        connection_id: "connection-1",
        display_name: "学校钉钉",
        capabilities: { "entity.teacher.read": true },
        visibility_summary: { visible: true, teacher_count: 5 },
      });
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-api",
        status: "active",
        messages: [],
        intent: {
          title: "全校组织数据同步",
          entity_types: [],
        },
        api_connection: {
          provider_id: "dingtalk",
          state: "configuration_required",
          required_secret_fields: ["app_key", "app_secret"],
          display_name: "钉钉临时连接-20260731-094800",
          capabilities: {},
          visibility_summary: {},
        },
        task: null,
      }),
      configureApiConnection,
    });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    const card = await screen.findByLabelText("API 连接配置");
    expect(within(card).getByLabelText("连接名称")).toHaveValue(
      "钉钉临时连接-20260731-094800",
    );
    await user.clear(within(card).getByLabelText("连接名称"));
    await user.type(within(card).getByLabelText("连接名称"), "学校钉钉");
    await user.selectOptions(within(card).getByLabelText("人员类型"), "student");
    await user.clear(within(card).getByLabelText("根部门 ID"));
    await user.type(within(card).getByLabelText("根部门 ID"), "2");
    await user.type(within(card).getByLabelText("人员编号字段"), "student_number");
    await user.type(within(card).getByLabelText("班级字段"), "class_name");
    await user.type(within(card).getByLabelText("AppKey"), "ding-app");
    await user.type(within(card).getByLabelText("AppSecret"), "ding-secret");
    await user.click(within(card).getByRole("button", { name: "保存并测试连接" }));

    await waitFor(() => expect(configureApiConnection).toHaveBeenCalledWith({
      conversation_id: "conversation-api",
      provider_id: "dingtalk",
      display_name: "学校钉钉",
      required_secret_fields: ["app_key", "app_secret"],
      public_configuration: {
        person_entity_kind: "student",
        root_department_id: 2,
        number_field: "student_number",
        class_name_field: "class_name",
      },
      secret: { app_key: "ding-app", app_secret: "ding-secret" },
    }));
    expect(
      await within(card).findByText(
        "连接测试未通过：connector_permission_denied",
      ),
    ).toBeInTheDocument();

    await user.type(within(card).getByLabelText("AppKey"), "ding-app-fixed");
    await user.type(within(card).getByLabelText("AppSecret"), "ding-secret-fixed");
    await user.click(within(card).getByRole("button", { name: "保存并测试连接" }));

    expect(await within(card).findByText("连接测试通过")).toBeInTheDocument();
    expect(configureApiConnection).toHaveBeenLastCalledWith({
      conversation_id: "conversation-api",
      provider_id: "dingtalk",
      display_name: "学校钉钉",
      required_secret_fields: ["app_key", "app_secret"],
      public_configuration: {
        person_entity_kind: "student",
        root_department_id: 2,
        number_field: "student_number",
        class_name_field: "class_name",
      },
      secret: {
        app_key: "ding-app-fixed",
        app_secret: "ding-secret-fixed",
      },
      connection_id: "connection-1",
    });
    expect(screen.queryByText("ding-app")).not.toBeInTheDocument();
    expect(screen.queryByText("ding-secret")).not.toBeInTheDocument();
  });

  it("shows backend confirmation and locks ordinary input after task start", async () => {
    const backend = api();
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));

    const confirmationCard = await screen.findByLabelText("开始确认");
    expect(within(confirmationCard).getByText("第三方对象")).toBeInTheDocument();
    expect(within(confirmationCard).getByText("teacher-roster.csv")).toBeInTheDocument();
    expect(within(confirmationCard).getByText("同步数据")).toBeInTheDocument();
    expect(within(confirmationCard).getByText("教师")).toBeInTheDocument();
    expect(confirmationCard.querySelectorAll(".start-confirmation-details > div")).toHaveLength(2);
    expect(confirmationCard.querySelectorAll("dt")).toHaveLength(2);
    expect(confirmationCard.querySelectorAll("dd")).toHaveLength(2);
    expect(
      within(confirmationCard).queryByText("将同步三方系统与希沃魔方的教师数据"),
    ).not.toBeInTheDocument();
    expect(within(confirmationCard).queryByText("teacher")).not.toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "任务处理状态" }),
    ).toHaveClass("is-idle");

    await user.click(within(confirmationCard).getByRole("button", { name: "确认开始同步" }));

    expect((await screen.findAllByText(/数据接入/)).length).toBeGreaterThan(0);
    expect(
      screen.getByRole("complementary", { name: "任务处理状态" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "新建对话" }).parentElement)
      .toHaveClass("has-task-status");
    expect(screen.getByText("报告生成")).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeDisabled();
    expect(screen.getByRole("button", { name: "终止任务" })).toBeInTheDocument();
    expect(backend.startTask).toHaveBeenCalledWith(
      "conversation-1",
      expect.objectContaining({ title: "全校教师同步" }),
      expect.any(String),
    );
  });

  it("shows only the backend-cleaned origin for a conversation remote source", async () => {
    const submittedUrl = "https://data.example.test/roster.csv?secret=value";
    const backend = api({
      sendMessage: vi.fn().mockResolvedValue({
        accepted_message: "请同步 [远程CSV来源:data.example.test] 的学生",
        message: "已识别第三方学生 CSV，可以开始同步。",
        intent: {
          title: "远程学生同步",
          entity_types: ["student"],
          source: {
            kind: "remote_csv",
            remote_source_id: "remote-source-1",
            display_origin: "data.example.test",
          },
          target: { kind: "local", source_ref: "seewo/students.csv" },
        },
        start_confirmation: {
          title: "远程学生同步",
          summary: "将第三方学生 CSV 对齐到希沃数据。",
          entity_types: ["student"],
        },
      }),
    });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(
      screen.getByLabelText("对账目标"),
      `请同步 ${submittedUrl} 的学生`,
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(
      await screen.findByText("请同步 [远程CSV来源:data.example.test] 的学生"),
    ).toBeInTheDocument();
    const confirmationCard = await screen.findByLabelText("开始确认");
    expect(within(confirmationCard).getByText("第三方对象")).toBeInTheDocument();
    expect(within(confirmationCard).getByText("data.example.test")).toBeInTheDocument();
    expect(
      within(confirmationCard).queryByText("第三方来源：data.example.test"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/secret=value/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "确认开始同步" }));
    expect(backend.startTask).toHaveBeenCalledWith(
      "conversation-1",
      expect.objectContaining({
        source: {
          kind: "remote_csv",
          remote_source_id: "remote-source-1",
          display_origin: "data.example.test",
        },
      }),
      expect.any(String),
    );
  });

  it.each([
    {
      name: "database configuration",
      source: { kind: "database", configuration_id: "school-mysql-prod" },
      expectedLabel: "school-mysql-prod",
    },
    {
      name: "API configuration",
      source: { kind: "api", configuration_id: "school-api-prod" },
      expectedLabel: "school-api-prod",
    },
    {
      name: "missing source details",
      source: undefined,
      expectedLabel: "已选择的第三方数据",
    },
  ] satisfies Array<{
    name: string;
    source: AgentConnectorSelection | undefined;
    expectedLabel: string;
  }>)("uses the safe third-party label for $name", async ({ source, expectedLabel }) => {
    const backend = api({
      sendMessage: vi.fn().mockResolvedValue({
        accepted_message: "同步全校教师",
        message: "已整理好全校教师同步需求。",
        intent: {
          title: "全校教师同步",
          entity_types: ["teacher"],
          source,
          target: {
            kind: "local",
            source_ref: "seewo/teacher-roster.csv",
          },
        },
        start_confirmation: {
          title: "全校教师同步",
          summary: "将同步三方系统与希沃魔方的教师数据",
          entity_types: ["teacher"],
        },
      }),
    });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));

    const confirmationCard = await screen.findByLabelText("开始确认");
    expect(within(confirmationCard).getByText(expectedLabel)).toBeInTheDocument();
    expect(confirmationCard.querySelectorAll(".start-confirmation-details > div")).toHaveLength(2);
    expect(confirmationCard.querySelectorAll("dt")).toHaveLength(2);
    expect(confirmationCard.querySelectorAll("dd")).toHaveLength(2);
  });

  it("does not echo a rejected link before the backend accepts it", async () => {
    const backend = api({
      sendMessage: vi.fn().mockRejectedValue(
        new ApiError("第三方数据链接必须使用 HTTPS。", 422, "remote_source_https_required"),
      ),
    });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(
      screen.getByLabelText("对账目标"),
      "同步 http://data.example.test/roster.csv?secret=value",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(
      await screen.findByText("第三方数据链接必须使用 HTTPS。"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/secret=value/)).not.toBeInTheDocument();
    expect(screen.getByText("消息未被接受。")).toBeInTheDocument();
  });

  it("refreshes task history immediately after the conversation starts a task", async () => {
    const backend = api();
    const historyUpdated = vi.fn();
    window.addEventListener(TASK_HISTORY_UPDATED_EVENT, historyUpdated);
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));

    await waitFor(() => expect(historyUpdated).toHaveBeenCalledTimes(1));
    window.removeEventListener(TASK_HISTORY_UPDATED_EVENT, historyUpdated);
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

  it("shows the current identity conflict and completes clarification inside chat", async () => {
    const submitClarificationSelection = vi.fn().mockResolvedValue({
      decision_id: "clarification-1",
      status: "interpreted",
      task_id: "task-identity",
      decision: "select_candidate",
      selected_candidate_id: "candidate-1",
      interpretation_zh: "你选择了第三方候选 A，确认后继续。",
      requires_second_confirmation: true,
    });
    const confirmClarification = vi.fn().mockResolvedValue({ status: "confirmed" });
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-identity",
        status: "active",
        messages: [],
        task: {
          id: "task-identity",
          workflow_version: "agent-graph-v1",
          phase: "clarify_identity_conflicts",
          status: "waiting_human",
        },
      }),
      graph: vi.fn().mockResolvedValue({
        task_id: "task-identity",
        workflow_version: "agent-graph-v1",
        graph_version: "agent-controlled-graph-v1",
        graph_cursor: 6,
        current_node: "resolve_identity_conflicts",
        business_stage: "agent_analysis",
        current_action_zh: "正在等待身份冲突说明",
        status: "waiting_human",
        can_terminate: true,
        termination_requested: false,
        human_gates: [{
          id: "identity-gate-1",
          kind: "identity_conflict",
          status: "pending",
          item_count: 1,
          cursor: 5,
          actionable: true,
          conflicts: [{
            clarification_id: "clarification-1",
            status: "pending",
            summary_zh: "唯一身份字段命中了多个第三方权威候选，Agent 无法安全选择。",
            subject: {
              entity_kind: "student",
              category: "student",
              name: "测试学生",
              number: "S-009",
              class_name: "一年级一班",
              phone_masked: "***0009",
              email_masked: "s***@example.test",
            },
            candidates: [
              {
                candidate_id: "candidate-1",
                entity_kind: "student",
                category: "student",
                name: "测试学生",
                number: "S-001",
                class_name: "一年级一班",
                phone_masked: "***0001",
                email_masked: "s***@example.test",
              },
              {
                candidate_id: "candidate-2",
                entity_kind: "student",
                category: "student",
                name: "测试学生二号",
                number: "S-002",
                class_name: "一年级二班",
                phone_masked: "***0002",
                email_masked: "s***@example.test",
              },
            ],
            allowed_outcomes: ["use_candidate", "target_extra"],
            interpretation_zh: null,
          }],
        }],
      }),
      submitClarificationSelection,
      confirmClarification,
    });
    const user = userEvent.setup();

    render(<ConversationCreatePage agentApi={backend} />);

    expect(await screen.findByText("第 1/1 条")).toBeInTheDocument();
    expect(screen.getByText("希沃记录")).toBeInTheDocument();
    expect(screen.getByText("第三方候选 A")).toBeInTheDocument();
    expect(screen.getByText("第三方候选 B")).toBeInTheDocument();
    expect(screen.getByText("S-009")).toBeInTheDocument();
    expect(screen.getByText("S-001")).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: "采用第三方候选 A" }));
    await user.type(screen.getByLabelText("补充说明（可选）"), "在对话页选择候选");
    await user.click(screen.getByRole("button", { name: "提交选择" }));

    expect(submitClarificationSelection).toHaveBeenCalledWith(
      "task-identity",
      "clarification-1",
      expect.objectContaining({
        decision: "select_candidate",
        selected_candidate_id: "candidate-1",
        note: "在对话页选择候选",
        graph_cursor: 6,
      }),
    );
    expect(await screen.findByText("等待确认")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认选择并继续" }));
    expect(confirmClarification).toHaveBeenCalledWith(
      "task-identity",
      "clarification-1",
    );
  });

  it("restores submitted clarification feedback without reopening duplicate submission", async () => {
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-identity",
        status: "active",
        messages: [],
        task: {
          id: "task-identity",
          workflow_version: "agent-graph-v1",
          phase: "clarify_identity_conflicts",
          status: "waiting_human",
        },
      }),
      graph: vi.fn().mockResolvedValue(identityGraph(
        "pending",
        "当前说明无法唯一确定候选，请明确选择候选 A 或按希沃多余处理。",
      )),
    });
    const user = userEvent.setup();

    render(<ConversationCreatePage agentApi={backend} />);

    expect(
      await screen.findByText("当前说明无法唯一确定候选，请明确选择候选 A 或按希沃多余处理。"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeDisabled();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "补充说明" }));
    expect(screen.getByLabelText("对账目标")).toBeDisabled();
    expect(screen.getByRole("radio", { name: "采用第三方候选 A" })).not.toBeChecked();
    expect(screen.getByLabelText("补充说明（可选）")).toHaveValue("");
  });

  it("does not reopen a confirmed clarification from a stale graph response", async () => {
    const submitClarificationSelection = vi.fn().mockResolvedValue({
      decision_id: "clarification-1",
      status: "interpreted",
      task_id: "task-identity",
      decision: "select_candidate",
      selected_candidate_id: "candidate-1",
      interpretation_zh: "你选择了第三方候选 A，确认后继续。",
      requires_second_confirmation: true,
    });
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-identity",
        status: "active",
        messages: [],
        task: {
          id: "task-identity",
          workflow_version: "agent-graph-v1",
          phase: "clarify_identity_conflicts",
          status: "waiting_human",
        },
      }),
      graph: vi.fn().mockResolvedValue(identityGraph()),
      submitClarificationSelection,
      confirmClarification: vi.fn().mockResolvedValue({ status: "confirmed" }),
    });
    const user = userEvent.setup();

    render(<ConversationCreatePage agentApi={backend} />);

    await screen.findByRole("radio", { name: "采用第三方候选 A" });
    await user.click(screen.getByRole("radio", { name: "采用第三方候选 A" }));
    await user.click(screen.getByRole("button", { name: "提交选择" }));
    await user.click(await screen.findByRole("button", { name: "确认选择并继续" }));

    expect(await screen.findByText("身份冲突选择已确认，Agent 正在继续处理。")).toBeInTheDocument();
    await new Promise((resolve) => window.setTimeout(resolve, 1_700));
    expect(screen.queryByRole("region", { name: "身份冲突处理" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeDisabled();
  });

  it("keeps legacy clarification compatible with its minimal response", async () => {
    const clarify = vi.fn().mockResolvedValue({
      decision_id: "legacy-decision",
      status: "interpreted",
      task_id: "task-legacy",
    });
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-legacy",
        status: "active",
        messages: [],
        task: {
          id: "task-legacy",
          workflow_version: "new-agent-v1",
          phase: "clarify_identity_conflicts",
          status: "waiting_human",
        },
      }),
      events: vi.fn().mockResolvedValue({
        cursor: "legacy-cursor",
        events: [{
          id: "legacy-conflict",
          cursor: "legacy-cursor",
          type: "clarification_required",
          phase: "clarify_identity_conflicts",
          status: "waiting_human",
          payload: { masked_evidence: "已遮罩的身份冲突证据" },
          created_at: "",
        }],
      }),
      clarify,
    });
    const user = userEvent.setup();

    render(<ConversationCreatePage agentApi={backend} />);

    await waitFor(() => expect(screen.getByLabelText("对账目标")).toBeEnabled());
    await user.type(screen.getByLabelText("对账目标"), "这两条记录属于同一个人。");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(clarify).toHaveBeenCalledWith(
      "task-legacy",
      "这两条记录属于同一个人。",
    );
    expect(
      await screen.findByText("已提交澄清，等待后端生成结构化决策确认。"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeDisabled();
  });

  it("shows compact SQL high-risk changes in chat and approves the frozen gate", async () => {
    const decideGraphGate = vi.fn().mockResolvedValue({
      gate_id: "gate-high-1",
      status: "approved",
      graph_cursor: 9,
    });
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-sql-risk",
        status: "active",
        messages: [
          { id: "message-1", role: "user", kind: "normal", text: "同步 MySQL 数据", created_at: "" },
        ],
        task: {
          id: "task-sql-risk",
          workflow_version: "agent-graph-v1",
          phase: "execute_and_verify",
          status: "waiting_human",
        },
      }),
      events: vi.fn().mockResolvedValue({
        cursor: "approval-event",
        events: [{
          id: "approval-required",
          cursor: "approval-event",
          type: "approval_required",
          payload: { group_id: "legacy-group" },
          created_at: "",
        }],
      }),
      graph: vi.fn().mockResolvedValue({
        task_id: "task-sql-risk",
        workflow_version: "agent-graph-v1",
        graph_version: "agent-controlled-graph-v1",
        graph_cursor: 9,
        current_node: "wait_high_risk_approvals",
        business_stage: "governance_execution",
        current_action_zh: "等待高风险操作审批",
        status: "waiting_human",
        can_terminate: true,
        termination_requested: false,
        human_gates: [{
          id: "gate-high-1",
          kind: "high_risk_approval",
          status: "pending",
          item_count: 1,
          risk: "high",
          cursor: 8,
          membership_hash: "membership-high-1",
          entity_kind: "teacher",
          operation: "delete",
          actionable: true,
          items: [{
            finding_id: "finding-high-1",
            entity_kind: "teacher",
            entity_name: "王老师",
            entity_number: "T-001",
            source_locator: "database:seewo-mysql:T-001",
            operation_zh: "删除教师记录",
            issue_zh: "希沃多余",
            analysis_zh: "这段分析不应出现在聊天记录。",
            solution_zh: "这段方案也不应出现在聊天记录。",
            changes: [{
              field: "phone",
              field_zh: "电话",
              before: "13800000001",
              after: null,
            }],
          }],
        }],
      }),
      decideGraphGate,
    });
    const user = userEvent.setup();

    render(<ConversationCreatePage agentApi={backend} />);

    expect(await screen.findByText("王老师（T-001）")).toBeInTheDocument();
    expect(screen.getByText("删除教师记录")).toBeInTheDocument();
    expect(screen.getByText("13800000001")).toBeInTheDocument();
    expect(screen.getByText("空值")).toBeInTheDocument();
    expect(screen.queryByText("这段分析不应出现在聊天记录。")).not.toBeInTheDocument();
    expect(screen.queryByText("这段方案也不应出现在聊天记录。")).not.toBeInTheDocument();
    expect(screen.queryByText("等待高风险操作审批")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "同意高风险操作" }));

    expect(decideGraphGate).toHaveBeenCalledWith(
      "task-sql-risk",
      "gate-high-1",
      "approve",
      "操作人通过聊天窗口同意高风险治理操作",
      {
        approved_finding_ids: ["finding-high-1"],
        rejected_finding_ids: [],
        graph_cursor: 9,
        membership_hash: "membership-high-1",
      },
    );
    expect(await screen.findByText("已同意")).toBeInTheDocument();
  });

  it("keeps a SQL high-risk gate visible after rejection", async () => {
    const decideGraphGate = vi.fn().mockResolvedValue({
      gate_id: "gate-high-2",
      status: "rejected",
      graph_cursor: 4,
    });
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-sql-reject",
        status: "active",
        messages: [],
        task: {
          id: "task-sql-reject",
          workflow_version: "agent-graph-v1",
          phase: "execute_and_verify",
          status: "waiting_human",
        },
      }),
      graph: vi.fn().mockResolvedValue({
        task_id: "task-sql-reject",
        workflow_version: "agent-graph-v1",
        graph_version: "agent-controlled-graph-v1",
        graph_cursor: 4,
        current_node: "wait_high_risk_approvals",
        business_stage: "governance_execution",
        current_action_zh: "等待高风险操作审批",
        status: "waiting_human",
        can_terminate: true,
        termination_requested: false,
        human_gates: [{
          id: "gate-high-2",
          kind: "high_risk_approval",
          status: "pending",
          item_count: 1,
          risk: "high",
          cursor: 3,
          membership_hash: "membership-high-2",
          entity_kind: "student",
          operation: "update",
          actionable: true,
          items: [{
            finding_id: "finding-high-2",
            entity_kind: "student",
            entity_name: "李同学",
            entity_number: "S-002",
            source_locator: "database:seewo-mysql:S-002",
            operation_zh: "修改学生手机号",
            issue_zh: "手机号错误",
            analysis_zh: "隐藏分析",
            solution_zh: "隐藏方案",
            changes: [{
              field: "phone",
              field_zh: "电话",
              before: "13800000002",
              after: "13900000002",
            }],
          }],
        }],
      }),
      decideGraphGate,
    });
    const user = userEvent.setup();

    render(<ConversationCreatePage agentApi={backend} />);
    await user.click(await screen.findByRole("button", { name: "拒绝高风险操作" }));

    expect(await screen.findByText("已拒绝")).toBeInTheDocument();
    expect(screen.getByText("李同学（S-002）")).toBeInTheDocument();
  });

  it("shows all medium-risk findings in one chat review and submits one frozen batch", async () => {
    const decideGraphGates = vi.fn().mockResolvedValue({
      decisions: [
        { gate_id: "gate-medium-1", status: "rejected", graph_cursor: 13 },
        { gate_id: "gate-medium-2", status: "approved", graph_cursor: 13 },
      ],
    });
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-medium-review",
        status: "active",
        messages: [],
        task: {
          id: "task-medium-review",
          workflow_version: "agent-graph-v1",
          phase: "execute_and_verify",
          status: "waiting_human",
        },
      }),
      graph: vi.fn().mockResolvedValue({
        task_id: "task-medium-review",
        workflow_version: "agent-graph-v1",
        graph_version: "agent-controlled-graph-v1",
        graph_cursor: 13,
        current_node: "wait_medium_risk_review",
        business_stage: "governance_execution",
        current_action_zh: "等待中风险批量复核",
        status: "waiting_human",
        can_terminate: true,
        termination_requested: false,
        human_gates: [
          {
            id: "gate-medium-1",
            kind: "high_risk_approval",
            status: "pending",
            item_count: 1,
            risk: "medium",
            cursor: 12,
            membership_hash: "membership-medium-1",
            actionable: true,
            items: [{
              finding_id: "finding-medium-1",
              entity_kind: "teacher",
              entity_name: "张老师",
              entity_number: "T-001",
              source_locator: "database:seewo:T-001",
              operation_zh: "修改教师邮箱",
              issue_zh: "邮箱不一致",
              analysis_zh: "隐藏分析",
              solution_zh: "隐藏方案",
              changes: [{
                field: "email",
                field_zh: "邮箱",
                before: "old@example.test",
                after: "new@example.test",
              }],
            }],
          },
          {
            id: "gate-medium-2",
            kind: "high_risk_approval",
            status: "pending",
            item_count: 1,
            risk: "medium",
            cursor: 12,
            membership_hash: "membership-medium-2",
            actionable: true,
            items: [{
              finding_id: "finding-medium-2",
              entity_kind: "department",
              entity_name: "教务处",
              entity_number: "D-001",
              source_locator: "database:seewo:D-001",
              operation_zh: "修改部门名称",
              issue_zh: "名称不一致",
              analysis_zh: "隐藏分析",
              solution_zh: "隐藏方案",
              changes: [{
                field: "name",
                field_zh: "名称",
                before: "教导处",
                after: "教务处",
              }],
            }],
          },
        ],
      }),
      decideGraphGates,
    });
    const user = userEvent.setup();

    render(<ConversationCreatePage agentApi={backend} />);

    const review = await screen.findByRole("region", { name: "中风险批量审核" });
    expect(review).toHaveTextContent("张老师（T-001）");
    expect(review).toHaveTextContent("教务处（D-001）");
    expect(screen.getAllByRole("checkbox", { name: /拒绝/ })).toHaveLength(2);
    await user.click(screen.getByRole("checkbox", { name: "拒绝张老师（T-001）" }));
    await user.click(screen.getByRole("button", { name: "按当前选择继续（同意 1，拒绝 1）" }));

    expect(decideGraphGates).toHaveBeenCalledWith("task-medium-review", [
      {
        gate_id: "gate-medium-1",
        decision: "reject",
        reason: "操作人通过聊天窗口完成中风险批量复核",
        approved_finding_ids: [],
        rejected_finding_ids: ["finding-medium-1"],
        graph_cursor: 13,
        membership_hash: "membership-medium-1",
      },
      {
        gate_id: "gate-medium-2",
        decision: "approve",
        reason: "操作人通过聊天窗口完成中风险批量复核",
        approved_finding_ids: ["finding-medium-2"],
        rejected_finding_ids: [],
        graph_cursor: 13,
        membership_hash: "membership-medium-2",
      },
    ]);
  });

  it("restores a previously approved SQL high-risk gate as read-only", async () => {
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-sql-approved",
        status: "active",
        messages: [],
        task: {
          id: "task-sql-approved",
          workflow_version: "agent-graph-v1",
          phase: "execute_and_verify",
          status: "waiting_human",
        },
      }),
      graph: vi.fn().mockResolvedValue({
        task_id: "task-sql-approved",
        workflow_version: "agent-graph-v1",
        graph_version: "agent-controlled-graph-v1",
        graph_cursor: 6,
        current_node: "wait_high_risk_approvals",
        business_stage: "governance_execution",
        current_action_zh: "等待高风险操作审批",
        status: "waiting_human",
        can_terminate: true,
        termination_requested: false,
        human_gates: [{
          id: "gate-high-approved",
          kind: "high_risk_approval",
          status: "approved",
          item_count: 1,
          risk: "high",
          cursor: 6,
          membership_hash: "membership-high-approved",
          entity_kind: "teacher",
          operation: "delete",
          actionable: false,
          items: [{
            finding_id: "finding-high-approved",
            entity_kind: "teacher",
            entity_name: "周老师",
            entity_number: "T-003",
            source_locator: "database:seewo-mysql:T-003",
            operation_zh: "删除教师记录",
            issue_zh: "希沃多余",
            analysis_zh: "隐藏分析",
            solution_zh: "隐藏方案",
            changes: [{
              field: "phone",
              field_zh: "电话",
              before: "13800000003",
              after: null,
            }],
          }],
        }],
      }),
    });

    render(<ConversationCreatePage agentApi={backend} />);

    expect(await screen.findByText("周老师（T-003）")).toBeInTheDocument();
    expect(screen.getByText("已同意")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "同意高风险操作" })).not.toBeInTheDocument();
  });

  it("keeps the backend intent available after a lock conflict", async () => {
    const backend = api({
      startTask: vi.fn().mockRejectedValue(
        new ApiError("School already has an active Agent task", 409, "school_lock_conflict"),
      ),
    });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));

    expect(await screen.findByText(
      "当前学校已有同步或回滚任务正在运行，请先在左侧任务记录中打开并完成或终止该任务。",
    )).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认开始同步" })).toBeInTheDocument();
  });

  it("asks before accepting a target file changed outside the Agent", async () => {
    const startTask = vi.fn()
      .mockRejectedValueOnce(
        new ApiError(
          "希沃目标文件已在 Agent 之外发生变化",
          409,
          "target_baseline_drift",
        ),
      )
      .mockResolvedValueOnce({
        id: "task-accepted-baseline",
        workflow_version: "agent-graph-v1",
        phase: "ingest_and_normalize",
        status: "running",
      });
    const backend = api({ startTask });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "重新同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));

    expect(
      await screen.findByText("希沃目标文件已在 Agent 之外发生变化"),
    ).toBeInTheDocument();
    expect(startTask).toHaveBeenCalledTimes(1);

    await user.click(
      screen.getByRole("button", { name: "将当前文件作为新基线继续" }),
    );

    expect(startTask).toHaveBeenLastCalledWith(
      "conversation-1",
      expect.objectContaining({ title: "全校教师同步" }),
      expect.any(String),
      { acceptCurrentTargetBaseline: true },
    );
    expect(
      await screen.findByText("任务已开始，我会持续同步后端进度。普通输入已锁定。"),
    ).toBeInTheDocument();
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
    expect(screen.getAllByText(/Agent 分析/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "开启新对话" })).toBeDisabled();
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
        intent: {
          title: "全校数据同步",
          entity_types: ["student", "department", "teacher"],
          source: {
            kind: "local",
            source_ref: "third-party/all-school-data.csv",
          },
          target: {
            kind: "local",
            source_ref: "seewo/all-school-data.csv",
          },
        },
        start_confirmation: {
          title: "全校数据同步",
          summary: "已确认同步需求。",
          entity_types: ["student", "department", "teacher"],
        },
        task: null,
      }),
    } as Partial<AgentConversationApi>);

    render(<ConversationCreatePage agentApi={backend} />);

    expect(await screen.findByRole("button", { name: "确认开始同步" })).toBeInTheDocument();
    const confirmationCard = screen.getByLabelText("开始确认");
    expect(within(confirmationCard).getByText("all-school-data.csv")).toBeInTheDocument();
    expect(
      within(confirmationCard).getByText("全部（部门、教师、学生）"),
    ).toBeInTheDocument();
    expect(within(confirmationCard).queryByText("已确认同步需求。")).not.toBeInTheDocument();
    expect(screen.getAllByText("已确认同步需求。")).toHaveLength(1);
  });

  it("keeps a failed task visible and unlocks the conversation without a new confirmation", async () => {
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-failed",
        status: "active",
        messages: [
          { id: "message-1", role: "user", kind: "normal", text: "同步 MySQL 数据", created_at: "" },
          { id: "message-2", role: "assistant", kind: "normal", text: "任务已经开始。", created_at: "" },
        ],
        intent: { title: "MySQL 数据同步", entity_types: ["student"] },
        start_confirmation: null,
        task: {
          id: "task-failed",
          workflow_version: "agent-graph-v1",
          phase: "ingest_and_normalize",
          status: "failed",
        },
      }),
    } as Partial<AgentConversationApi>);

    render(<ConversationCreatePage agentApi={backend} />);

    expect((await screen.findAllByText("任务处理失败")).length).toBeGreaterThan(0);
    expect(
      screen.getByRole("complementary", { name: "任务处理状态" }),
    ).not.toHaveClass("is-idle");
    expect(screen.queryByRole("button", { name: "确认开始同步" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeEnabled();
    expect(screen.getByRole("button", { name: "开启新对话" })).toBeEnabled();
  });

  it("continues the same conversation and starts a sequential task after completion", async () => {
    const sendMessage = vi.fn().mockResolvedValue({
      accepted_message: "再同步一次教师数据",
      message: "已准备下一次教师同步。",
      intent: { title: "下一次教师同步", entity_types: ["teacher"] },
      start_confirmation: {
        title: "下一次教师同步",
        summary: "继续沿用当前对话，启动新的教师同步任务。",
        entity_types: ["teacher"],
      },
    });
    const startTask = vi.fn().mockResolvedValue({
      id: "task-next",
      workflow_version: "agent-graph-v1",
      phase: "ingest_and_normalize",
      status: "running",
    });
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-sequential",
        status: "active",
        messages: [
          { id: "message-old", role: "assistant", kind: "normal", text: "上一次任务已经完成。", created_at: "" },
        ],
        task: {
          id: "task-completed",
          workflow_version: "agent-graph-v1",
          phase: "terminal",
          status: "completed",
        },
      }),
      sendMessage,
      startTask,
    });
    const user = userEvent.setup();

    render(<ConversationCreatePage agentApi={backend} />);

    expect(await screen.findByText("上一次任务已经完成。")).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeEnabled();
    await user.type(screen.getByLabelText("对账目标"), "再同步一次教师数据");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByRole("button", { name: "确认开始同步" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Agent 任务进度")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认开始同步" }));

    expect(sendMessage).toHaveBeenCalledWith(
      "conversation-sequential",
      "再同步一次教师数据",
    );
    expect(startTask).toHaveBeenCalledWith(
      "conversation-sequential",
      expect.objectContaining({
        title: "下一次教师同步",
        entity_types: ["teacher"],
      }),
      expect.any(String),
    );
    expect(await screen.findByText("任务已开始，我会持续同步后端进度。普通输入已锁定。")).toBeInTheDocument();
  });

  it("does not carry an old cursor or clarification into the next sequential task", async () => {
    const clarify = vi.fn();
    const sendMessage = vi.fn().mockResolvedValue({
      accepted_message: "再同步一次学生数据",
      message: "已准备下一次同步。",
      intent: { title: "下一次同步", entity_types: ["student"] },
      start_confirmation: {
        title: "下一次同步",
        summary: "开始新的学生同步。",
        entity_types: ["student"],
      },
    });
    const events = vi.fn().mockImplementation(
      (taskId: string) => Promise.resolve(
        taskId === "task-old"
          ? {
              cursor: "old-cursor",
              events: [{
                id: "old-clarification",
                cursor: "old-cursor",
                type: "clarification_required",
                phase: "clarify_identity_conflicts",
                status: "terminated",
                payload: { masked_evidence: "旧任务冲突" },
                created_at: "",
              }],
            }
          : { cursor: "new-cursor", events: [] },
      ),
    );
    const task = vi.fn().mockImplementation((taskId: string) => Promise.resolve(
      taskId === "task-old"
        ? {
            id: "task-old",
            workflow_version: "new-agent-v1",
            phase: "terminal",
            status: "terminated",
          }
        : undefined,
    ));
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-clean-sequence",
        status: "active",
        messages: [],
        task: {
          id: "task-old",
          workflow_version: "new-agent-v1",
          phase: "clarify_identity_conflicts",
          status: "running",
        },
      }),
      events,
      task,
      clarify,
      sendMessage,
      startTask: vi.fn().mockResolvedValue({
        id: "task-new",
        workflow_version: "agent-graph-v1",
        phase: "ingest_and_normalize",
        status: "running",
      }),
    });
    const user = userEvent.setup();

    render(<ConversationCreatePage agentApi={backend} />);

    await waitFor(() => expect(task).toHaveBeenCalledWith("task-old"));
    await waitFor(() => expect(screen.getByLabelText("对账目标")).toBeEnabled());
    await user.type(screen.getByLabelText("对账目标"), "开始下一次学生同步");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(sendMessage).toHaveBeenCalledWith(
      "conversation-clean-sequence",
      "开始下一次学生同步",
    );
    expect(clarify).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));

    await waitFor(() => {
      expect(events).toHaveBeenCalledWith("task-new", undefined);
    });
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

  it("dismisses and refreshes a stale graph termination confirmation", async () => {
    const previewTermination = vi.fn().mockResolvedValue({
      id: "termination-gate-stale",
      kind: "termination_confirmation",
      status: "pending",
      item_count: 1,
    });
    const decideGraphGate = vi.fn().mockRejectedValue(
      new ApiError(
        "Gate is already decided",
        409,
        "graph_gate_already_decided",
      ),
    );
    const task = vi.fn().mockResolvedValue({
      id: "task-graph-stale",
      workflow_version: "agent-graph-v1",
      phase: "analyze_batches",
      status: "running",
    });
    const graph = vi.fn().mockResolvedValue({
      task_id: "task-graph-stale",
      workflow_version: "agent-graph-v1",
      graph_version: "agent-sync-graph-v1",
      graph_cursor: 4,
      current_node: "analyze_batches",
      business_stage: "agent_analysis",
      current_action_zh: "正在分析数据",
      status: "running",
      can_terminate: true,
      termination_requested: false,
      human_gates: [],
    });
    const backend = api({
      startTask: vi.fn().mockResolvedValue({
        id: "task-graph-stale",
        workflow_version: "agent-graph-v1",
        phase: "ingest_and_normalize",
        status: "running",
      }),
      previewTermination,
      decideGraphGate,
      task,
      graph,
    } as Partial<AgentConversationApi>);
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));
    await user.click(await screen.findByRole("button", { name: "终止任务" }));
    await screen.findByRole("dialog", { name: "确认终止当前任务？" });
    task.mockClear();
    graph.mockClear();

    await user.click(screen.getByRole("button", { name: "确认终止" }));

    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "确认终止当前任务？" }),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByText("Gate is already decided")).toBeInTheDocument();
    expect(task).toHaveBeenCalledWith("task-graph-stale");
    expect(graph).toHaveBeenCalledWith("task-graph-stale");
  });

  it("keeps the termination confirmation retryable after a non-conflict error", async () => {
    const backend = api({
      startTask: vi.fn().mockResolvedValue({
        id: "task-graph-error",
        workflow_version: "agent-graph-v1",
        phase: "ingest_and_normalize",
        status: "running",
      }),
      previewTermination: vi.fn().mockResolvedValue({
        id: "termination-gate-error",
        kind: "termination_confirmation",
        status: "pending",
        item_count: 1,
      }),
      decideGraphGate: vi.fn().mockRejectedValue(
        new ApiError("后端处理请求失败", 500),
      ),
    } as Partial<AgentConversationApi>);
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));
    await user.click(await screen.findByRole("button", { name: "终止任务" }));
    await screen.findByRole("dialog", { name: "确认终止当前任务？" });
    await user.click(screen.getByRole("button", { name: "确认终止" }));

    expect(
      await screen.findByRole("dialog", { name: "确认终止当前任务？" }),
    ).toBeInTheDocument();
    expect(screen.getByText("后端处理请求失败")).toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: "开启新对话" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "终止任务" })).not.toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "任务处理状态" }),
    ).not.toHaveClass("is-idle");
  });

  it("retains the task progress card when polling reports a failure", async () => {
    const task = vi.fn().mockResolvedValue({
      id: "task-failed",
      workflow_version: "agent-graph-v1",
      phase: "ingest_and_normalize",
      status: "failed",
    });
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-running",
        status: "active",
        messages: [
          { id: "message-1", role: "user", kind: "normal", text: "同步 MySQL 数据", created_at: "" },
        ],
        task: {
          id: "task-failed",
          workflow_version: "agent-graph-v1",
          phase: "ingest_and_normalize",
          status: "running",
        },
      }),
      task,
      events: vi.fn().mockResolvedValue({
        cursor: "2",
        events: [{
          id: "event-failed",
          cursor: "2",
          type: "run.failed",
          phase: "ingest_and_normalize",
          status: "failed",
          payload: { message: "任务状态保存失败。" },
          created_at: "",
        }],
      }),
    });

    render(<ConversationCreatePage agentApi={backend} />);

    expect((await screen.findAllByText("任务处理失败")).length).toBeGreaterThan(0);
    expect(screen.getByText("任务状态保存失败。")).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "任务处理状态" }),
    ).not.toHaveClass("is-idle");
    expect(screen.getByLabelText("对账目标")).toBeEnabled();
    expect(screen.getByRole("button", { name: "开启新对话" })).toBeEnabled();
  });

  it("aligns user and assistant messages semantically around the persistent status rail", async () => {
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-layout",
        status: "active",
        messages: [
          { id: "assistant-message", role: "assistant", kind: "normal", text: "我在左侧回答。", created_at: "" },
          { id: "user-message", role: "user", kind: "normal", text: "我在右侧提问。", created_at: "" },
        ],
        task: {
          id: "task-layout",
          workflow_version: "agent-graph-v1",
          phase: "analyze_batches",
          status: "running",
        },
      }),
    });

    render(<ConversationCreatePage agentApi={backend} />);

    expect(await screen.findByRole("article", { name: "同步助手消息" })).toHaveClass("assistant");
    expect(screen.getByRole("article", { name: "你的消息" })).toHaveClass("user");
    expect(screen.getByRole("complementary", { name: "任务处理状态" })).toBeInTheDocument();
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
        new ApiError(
          "对话模型暂时无法生成有效回复，请稍后重试。",
          502,
          "conversation_model_error",
        ),
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
    expect(screen.getByRole("article", { name: "你的消息" }))
      .toHaveTextContent("你是谁");
    expect(screen.queryByText("消息未被接受。"))
      .not.toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeEnabled();
  });

  it("emphasizes new conversation when complete context reaches the model limit", async () => {
    const backend = api({
      sendMessage: vi.fn().mockRejectedValue(
        new ApiError(
          "当前对话内容已达到模型处理上限，请开启新对话",
          409,
          "conversation_context_limit",
        ),
      ),
    });
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "继续使用完整历史");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(
      await screen.findByText("当前对话内容已达到模型处理上限，请开启新对话"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开启新对话" })).toHaveClass(
      "is-emphasized",
    );
  });
});

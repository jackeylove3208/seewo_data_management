import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentConversationApi } from "../../api/agent";
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

  it("shows backend confirmation and locks ordinary input after task start", async () => {
    const backend = api();
    const user = userEvent.setup();
    render(<ConversationCreatePage agentApi={backend} />);

    await waitForComposer();
    await user.type(screen.getByLabelText("对账目标"), "同步全校教师");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "确认开始同步" }));

    expect((await screen.findAllByText(/数据接入/)).length).toBeGreaterThan(0);
    expect(
      screen.getByRole("complementary", { name: "任务处理状态" }),
    ).toBeInTheDocument();
    expect(screen.getByText("报告生成")).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeDisabled();
    expect(screen.getByRole("button", { name: "终止任务" })).toBeInTheDocument();
    expect(backend.startTask).toHaveBeenCalledWith(
      "conversation-1",
      expect.objectContaining({ title: "全校教师同步" }),
      expect.any(String),
    );
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
    const clarify = vi.fn().mockResolvedValue({
      decision_id: "clarification-1",
      status: "interpreted",
      task_id: "task-identity",
      decision: "select_candidate",
      selected_candidate_id: "candidate-1",
      interpretation_zh: "我理解为选择第三方候选 A，确认后继续。",
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
              name: "测试学生",
              number: "S-009",
              class_name: "一年级一班",
              phone_masked: "***0009",
              email: "student@example.test",
            },
            candidates: [{
              entity_kind: "student",
              name: "测试学生",
              number: "S-001",
              class_name: "一年级一班",
              phone_masked: "138****0001",
              email: "student@example.test",
            }],
            allowed_outcomes: ["use_candidate", "target_extra"],
            interpretation_zh: null,
          }],
        }],
      }),
      clarify,
      confirmClarification,
    });
    const user = userEvent.setup();

    render(<ConversationCreatePage agentApi={backend} />);

    expect(await screen.findByText("第 1/1 条")).toBeInTheDocument();
    expect(screen.getByText("希沃记录")).toBeInTheDocument();
    expect(screen.getByText("第三方候选 A")).toBeInTheDocument();
    expect(screen.getByText("S-009")).toBeInTheDocument();
    expect(screen.getByText("S-001")).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeEnabled();

    await user.type(screen.getByLabelText("对账目标"), "请选择第三方候选 A。");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(clarify).toHaveBeenCalledWith("task-identity", "请选择第三方候选 A。");
    expect(await screen.findByText("我理解为选择第三方候选 A，确认后继续。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认模型解释" }));
    expect(confirmClarification).toHaveBeenCalledWith(
      "task-identity",
      "clarification-1",
    );
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

  it("keeps a failed task visible and ignores a stale restored confirmation", async () => {
    const backend = api({
      currentConversation: vi.fn().mockResolvedValue({
        id: "conversation-failed",
        status: "active",
        messages: [
          { id: "message-1", role: "user", kind: "normal", text: "同步 MySQL 数据", created_at: "" },
          { id: "message-2", role: "assistant", kind: "normal", text: "任务已经开始。", created_at: "" },
        ],
        intent: { title: "MySQL 数据同步", entity_types: ["student"] },
        start_confirmation: {
          title: "MySQL 数据同步",
          summary: "这是已经消费过的旧确认。",
          entity_types: ["student"],
        },
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
    expect(screen.getByRole("complementary", { name: "任务处理状态" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认开始同步" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeDisabled();
    expect(screen.getByRole("button", { name: "开启新对话" })).toBeEnabled();
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
    await waitFor(() => expect(screen.getByLabelText("对账目标")).toBeDisabled());
    expect(screen.getByRole("button", { name: "开启新对话" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "终止任务" })).not.toBeInTheDocument();
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
    expect(screen.getByRole("complementary", { name: "任务处理状态" })).toBeInTheDocument();
    expect(screen.getByLabelText("对账目标")).toBeDisabled();
    expect(screen.getByRole("button", { name: "开启新对话" })).toBeEnabled();
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

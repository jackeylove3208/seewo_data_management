import { Alert, Modal, Spin } from "antd";
import {
  ArrowUp,
  Bot,
  MessageSquarePlus,
  UserRound,
} from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  agentApi as defaultAgentApi,
  type AgentApiConnectionCard,
  type AgentClarificationSubmission,
  type AgentConversationApi,
  type AgentEntityType,
  type AgentGraphHumanGate,
  type AgentGraphProgress,
  type AgentIntent,
  type AgentStartConfirmation,
  type AgentTask,
  type AgentTaskEvent,
} from "../../api/agent";
import { ApiError } from "../../api/client";
import { TASK_HISTORY_UPDATED_EVENT } from "../../data/taskHistory";
import { IdentityConflictClarificationCard } from "../../components/IdentityConflictClarificationCard";
import { TaskStatusRail } from "../../components/TaskStatusRail";
import { presentAgentEvent, presentAgentPhase } from "../agent-events/presentation";
import { ConversationApiConnectionCard } from "./ConversationApiConnectionCard";
import { ConversationMediumRiskReviewCard } from "./ConversationMediumRiskReviewCard";
import { ConversationRiskApprovalCard } from "./ConversationRiskApprovalCard";

type ConversationState = "idle" | "collecting" | "needs-input" | "draft-ready" | "submitting" | "failed" | "created";
interface ConversationMessage {
  id: string;
  role: "assistant" | "user";
  text: string;
  kind?: "normal" | "guardrail" | "error";
}

const initialMessages: ConversationMessage[] = [{
  id: "assistant-welcome",
  role: "assistant",
  text: "你好，我是智能数据同步助手。告诉我希望同步的范围和对象。",
}];

const agentTaskStages = [
  { id: "ingest_and_normalize", label: "数据接入" },
  { id: "analyze_batches", label: "Agent 分析与决策" },
  { id: "execute_and_verify", label: "治理执行" },
  { id: "generate_report", label: "报告生成" },
];
const terminalTaskStatuses = new Set(["completed", "terminated", "failed"]);
const confirmationEntityOrder: AgentEntityType[] = ["department", "teacher", "student"];
const confirmationEntityLabel: Record<AgentEntityType, string> = {
  department: "部门",
  teacher: "教师",
  student: "学生",
};

function confirmationSourceLabel(intent?: AgentIntent) {
  const source = intent?.source;
  if (!source) return "已选择的第三方数据";
  if (source.kind === "remote_csv" && source.display_origin) {
    return source.display_origin;
  }
  if (source.source_ref) {
    const segments = source.source_ref.split(/[\\/]/).filter(Boolean);
    return segments[segments.length - 1] ?? source.source_ref;
  }
  return source.configuration_id ?? "已选择的第三方数据";
}

function confirmationEntities(entityTypes: AgentEntityType[]) {
  const selected = new Set(entityTypes);
  return confirmationEntityOrder
    .filter((entityType) => selected.has(entityType))
    .map((entityType) => confirmationEntityLabel[entityType])
    .join("、");
}

function taskStageIndex(phase: AgentTask["phase"]) {
  if (phase === "terminal" || phase === "report_restore") return agentTaskStages.length;
  if (phase === "generate_report" || phase === "plan_restore") return 3;
  if (
    phase === "aggregate_risk_and_approvals"
    || phase === "compile_execution_plan"
    || phase === "execute_and_verify"
    || phase === "clarify_restore_conflicts"
    || phase === "approve_restore"
    || phase === "execute_restore"
  ) return 2;
  if (
    phase === "analyze_batches"
    || phase === "clarify_identity_conflicts"
  ) return 1;
  return 0;
}

function messageId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function sessionKey() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function visibleIdentityGate(
  progress: AgentGraphProgress,
  confirmedClarificationIds: ReadonlySet<string>,
) {
  const persistedGate = progress.human_gates.find(
    (gate) => gate.kind === "identity_conflict" && gate.status === "pending",
  );
  const visibleConflicts = persistedGate?.conflicts?.filter(
    (conflict) => !confirmedClarificationIds.has(conflict.clarification_id),
  );
  return persistedGate && visibleConflicts?.length
    ? { ...persistedGate, conflicts: visibleConflicts }
    : undefined;
}

export function ConversationCreatePage({
  agentApi,
}: {
  agentApi?: AgentConversationApi;
}) {
  const [messages, setMessages] = useState<ConversationMessage[]>(initialMessages);
  const [input, setInput] = useState("");
  const [state, setState] = useState<ConversationState>("idle");
  const [conversationId, setConversationId] = useState<string>();
  const [confirmation, setConfirmation] = useState<AgentStartConfirmation>();
  const [agentIntent, setAgentIntent] = useState<AgentIntent>();
  const [apiConnection, setApiConnection] = useState<AgentApiConnectionCard>();
  const [task, setTask] = useState<AgentTask>();
  const [events, setEvents] = useState<AgentTaskEvent[]>([]);
  const [eventCursor, setEventCursor] = useState<string>();
  const [clarificationOpen, setClarificationOpen] = useState(false);
  const [identityGate, setIdentityGate] = useState<AgentGraphHumanGate>();
  const [handledApprovalGroups, setHandledApprovalGroups] = useState<string[]>([]);
  const [confirmedClarifications, setConfirmedClarifications] = useState<string[]>([]);
  const confirmedClarificationsRef = useRef(new Set<string>());
  const handledClarificationEvents = useRef(new Set<string>());
  const [terminationGate, setTerminationGate] = useState<AgentGraphHumanGate>();
  const [highRiskGates, setHighRiskGates] = useState<AgentGraphHumanGate[]>([]);
  const [mediumRiskGates, setMediumRiskGates] = useState<AgentGraphHumanGate[]>([]);
  const [graphCursor, setGraphCursor] = useState<number>();
  const [terminationLoading, setTerminationLoading] = useState(false);
  const [terminationError, setTerminationError] = useState<string>();
  const [hydrating, setHydrating] = useState(true);
  const [newConversationOpen, setNewConversationOpen] = useState(false);
  const [newConversationLoading, setNewConversationLoading] = useState(false);
  const [newConversationError, setNewConversationError] = useState<string>();
  const [contextLimitReached, setContextLimitReached] = useState(false);
  const [targetBaselineDrift, setTargetBaselineDrift] = useState(false);

  const backendApi = agentApi ?? defaultAgentApi;

  useEffect(() => {
    let cancelled = false;
    setHydrating(true);
    void backendApi.currentConversation()
      .then(async (current) => {
        if (cancelled) return;
        if (current) {
          setConversationId(current.id);
          setMessages(current.messages.length ? current.messages : initialMessages);
          setAgentIntent(current.intent ?? undefined);
          setApiConnection(current.api_connection ?? undefined);
          const restoredTask = current.task ?? undefined;
          setConfirmation(
            restoredTask && !terminalTaskStatuses.has(restoredTask.status)
              ? undefined
              : current.start_confirmation ?? undefined,
          );
          setTask(restoredTask);
          setState(
            restoredTask?.status === "failed"
              ? "failed"
              : restoredTask
                ? "created"
                : current.start_confirmation
                  ? "draft-ready"
                  : "idle",
          );
          return;
        }
        const conversation = await backendApi.createConversation();
        if (!cancelled) setConversationId(conversation.id);
      })
      .catch(() => {
        if (cancelled) return;
        setState("failed");
        setMessages((current) => [...current, {
          id: messageId(),
          role: "assistant",
          text: "对话服务暂时不可用，请稍后重试。",
          kind: "error",
        }]);
      })
      .finally(() => {
        if (!cancelled) setHydrating(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentApi, backendApi]);

  useEffect(() => {
    if (!task || terminalTaskStatuses.has(task.status) || !backendApi.events) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const [page, refreshedTask, graphProgress] = await Promise.all([
          backendApi.events(task.id, eventCursor),
          backendApi.task?.(task.id),
          task.workflow_version === "agent-graph-v1" && backendApi.graph
            ? backendApi.graph(task.id)
            : Promise.resolve(undefined),
        ]);
        if (cancelled) return;
        if (refreshedTask) {
          setTask((current) => current
            && current.phase === refreshedTask.phase
            && current.status === refreshedTask.status
            ? current
            : refreshedTask);
        }
        if (graphProgress) {
          setGraphCursor(graphProgress.graph_cursor);
          const refreshedGates = graphProgress.human_gates.filter(
            (gate) => gate.kind === "high_risk_approval" && gate.risk === "high",
          );
          setHighRiskGates((current) => {
            const merged = new Map(current.map((gate) => [gate.id, gate]));
            for (const gate of refreshedGates) merged.set(gate.id, gate);
            return [...merged.values()];
          });
          const refreshedMediumGates = graphProgress.human_gates.filter(
            (gate) => gate.kind === "high_risk_approval" && gate.risk === "medium",
          );
          setMediumRiskGates((current) => {
            const merged = new Map(current.map((gate) => [gate.id, gate]));
            for (const gate of refreshedMediumGates) merged.set(gate.id, gate);
            return [...merged.values()];
          });
          setIdentityGate(
            visibleIdentityGate(graphProgress, confirmedClarificationsRef.current),
          );
          setClarificationOpen(false);
        }
        setEventCursor(page.cursor);
        setEvents((current) => {
          const known = new Set(current.map((item) => item.id));
          return [...current, ...page.events.filter((item) => !known.has(item.id))];
        });
        const latest = page.events.at(-1);
        if (latest?.phase || latest?.status) {
          setTask((current) => current ? {
            ...current,
            phase: latest.phase ?? current.phase,
            status: latest.status ?? current.status,
          } : current);
        }
        if (
          latest?.type === "clarification_required"
          && task.workflow_version !== "agent-graph-v1"
          && !handledClarificationEvents.current.has(latest.id)
        ) {
          setClarificationOpen(true);
        }
        const terminalStatus = refreshedTask?.status
          ?? (terminalTaskStatuses.has(latest?.status ?? "") ? latest?.status : undefined);
        if (terminalStatus) {
          setState(terminalStatus === "failed" ? "failed" : "created");
        }
      } catch {
        // The persisted task remains visible; the next poll retries safely.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    backendApi,
    confirmedClarifications,
    eventCursor,
    task,
  ]);

  useEffect(() => {
    setHighRiskGates([]);
    setMediumRiskGates([]);
    setEvents([]);
    setEventCursor(undefined);
    setGraphCursor(undefined);
    setIdentityGate(undefined);
    setConfirmedClarifications([]);
    confirmedClarificationsRef.current.clear();
    handledClarificationEvents.current.clear();
  }, [task?.id]);

  const terminalTaskStatus = task && terminalTaskStatuses.has(task.status)
    ? task.status
    : undefined;

  useEffect(() => {
    if (!terminalTaskStatus) return;
    setClarificationOpen(false);
    setIdentityGate(undefined);
    setTerminationGate(undefined);
    setHighRiskGates((current) => current.map((gate) => (
      gate.status === "pending"
        ? {
            ...gate,
            actionable: false,
            unavailable_reason_zh: "任务已经结束，不能再提交审批。",
          }
        : gate
    )));
    setMediumRiskGates((current) => current.map((gate) => (
      gate.status === "pending"
        ? {
            ...gate,
            actionable: false,
            unavailable_reason_zh: "任务已经结束，不能再提交复核。",
          }
        : gate
    )));
  }, [terminalTaskStatus]);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (
      !message
      || hydrating
      || state === "collecting"
      || (taskActive && !clarificationOpen)
    ) return;
    setInput("");
    setState("collecting");
    const submittedMessageId = messageId();
    setMessages((current) => [...current, {
      id: submittedMessageId,
      role: "user",
      text: taskActive ? message : "消息已提交，正在安全处理。",
    }]);
    try {
      if (
        taskActive
        && task
        && task.workflow_version !== "agent-graph-v1"
        && clarificationOpen
        && backendApi.clarify
      ) {
        const interpretation = await backendApi.clarify(task.id, message);
        const clarificationEventId = events
          .slice()
          .reverse()
          .find((item) => item.type === "clarification_required")
          ?.id;
        if (clarificationEventId) {
          handledClarificationEvents.current.add(clarificationEventId);
        }
        setClarificationOpen(false);
        setState("created");
        setMessages((current) => [...current, {
          id: messageId(),
          role: "assistant",
          text: interpretation.requires_second_confirmation
            ? "已提交澄清，等待确认后继续。"
            : "已提交澄清，等待后端生成结构化决策确认。",
        }]);
        return;
      }
      if (task && terminalTaskStatuses.has(task.status)) {
        setTask(undefined);
      }
      setConfirmation(undefined);
      const response = await backendApi.sendMessage(
        conversationId ?? await createConversation(),
        message,
      );
      setContextLimitReached(false);
      setAgentIntent(response.intent);
      setApiConnection(response.api_connection ?? undefined);
      setMessages((current) => [
        ...current.map((item) => (
          item.id === submittedMessageId
            ? { ...item, text: response.accepted_message }
            : item
        )),
        {
          id: messageId(),
          role: "assistant",
          text: response.message,
        },
      ]);
      if (response.start_confirmation) {
        setConfirmation(response.start_confirmation);
      }
      setState(response.start_confirmation ? "draft-ready" : "needs-input");
    } catch (error) {
      setContextLimitReached(
        error instanceof ApiError && error.code === "conversation_context_limit",
      );
      setMessages((current) => [
        ...current.map((item) => (
          item.id === submittedMessageId && !taskActive
            ? { ...item, text: "消息未被接受。" }
            : item
        )),
        {
          id: messageId(),
          role: "assistant",
          text: error instanceof Error
            ? error.message
            : "没有理解这条要求，请换一种说法后重试。",
          kind: "error",
        },
      ]);
      setState("failed");
    }
  }

  async function confirmNewConversation() {
    if (taskActive || newConversationLoading) return;
    setNewConversationLoading(true);
    setNewConversationError(undefined);
    try {
      const conversation = await backendApi.resetConversation(sessionKey());
      setConversationId(conversation.id);
      setMessages(initialMessages);
      setInput("");
      setState("idle");
      setConfirmation(undefined);
      setAgentIntent(undefined);
      setApiConnection(undefined);
      setTask(undefined);
      setEvents([]);
      setEventCursor(undefined);
      setClarificationOpen(false);
      setIdentityGate(undefined);
      setHandledApprovalGroups([]);
      setConfirmedClarifications([]);
      confirmedClarificationsRef.current.clear();
      handledClarificationEvents.current.clear();
      setTerminationGate(undefined);
      setHighRiskGates([]);
      setMediumRiskGates([]);
      setGraphCursor(undefined);
      setTerminationError(undefined);
      setContextLimitReached(false);
      setNewConversationOpen(false);
    } catch (error) {
      setNewConversationError(
        error instanceof Error ? error.message : "开启新对话失败，请稍后重试",
      );
      setNewConversationOpen(false);
    } finally {
      setNewConversationLoading(false);
    }
  }

  async function createConversation() {
    if (conversationId) return conversationId;
    const conversation = await backendApi.createConversation();
    setConversationId(conversation.id);
    return conversation.id;
  }

  async function startTask(acceptCurrentTargetBaseline = false) {
    if (!confirmation || !conversationId || taskActive) return;
    setState("submitting");
    try {
      const intent = {
        title: agentIntent?.title || confirmation.title,
        entity_types: confirmation.entity_types,
        source: agentIntent?.source,
        target: agentIntent?.target,
      };
      const created = acceptCurrentTargetBaseline
        ? await backendApi.startTask(conversationId, intent, sessionKey(), {
          acceptCurrentTargetBaseline: true,
        })
        : await backendApi.startTask(conversationId, intent, sessionKey());
      setEvents([]);
      setEventCursor(undefined);
      setClarificationOpen(false);
      setIdentityGate(undefined);
      setHighRiskGates([]);
      setMediumRiskGates([]);
      setGraphCursor(undefined);
      setTask(created);
      setConfirmation(undefined);
      setTargetBaselineDrift(false);
      setState("created");
      window.dispatchEvent(new Event(TASK_HISTORY_UPDATED_EVENT));
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: "任务已开始，我会持续同步后端进度。普通输入已锁定。",
      }]);
    } catch (error) {
      setState("failed");
      const baselineDrift = error instanceof ApiError
        && error.code === "target_baseline_drift";
      setTargetBaselineDrift(baselineDrift);
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: baselineDrift && error instanceof Error
          ? error.message
          : error instanceof ApiError && error.code === "school_lock_conflict"
            ? "当前学校已有同步或回滚任务正在运行，请先在左侧任务记录中打开并完成或终止该任务。"
            : "任务启动失败，现有需求仍然保留，可以重试。",
        kind: "error",
      }]);
    }
  }

  async function terminateTask() {
    if (!task) return;
    setTerminationLoading(true);
    setTerminationError(undefined);
    try {
      if (task.workflow_version === "agent-graph-v1") {
        if (!backendApi.previewTermination) {
          throw new Error("当前客户端不支持受控任务终止确认");
        }
        setTerminationGate(await backendApi.previewTermination(task.id));
      } else {
        const result = await backendApi.terminate(task.id);
        setTask((current) => current ? { ...current, status: result.status } : current);
      }
    } catch (error) {
      setTerminationError(error instanceof Error ? error.message : "终止任务失败");
    } finally {
      setTerminationLoading(false);
    }
  }

  async function decideTermination(decision: "approve" | "reject") {
    if (!task || !terminationGate || !backendApi.decideGraphGate) return;
    setTerminationLoading(true);
    setTerminationError(undefined);
    try {
      await backendApi.decideGraphGate(
        task.id,
        terminationGate.id,
        decision,
        decision === "approve"
          ? "操作人确认终止当前任务"
          : "操作人取消终止当前任务",
      );
      setTerminationGate(undefined);
      if (decision === "approve") {
        setTask((current) => current ? { ...current, status: "terminating" } : current);
      }
    } catch (error) {
      setTerminationError(error instanceof Error ? error.message : "终止确认未完成");
    } finally {
      setTerminationLoading(false);
    }
  }

  function payloadText(event: AgentTaskEvent, key: string) {
    const value = event.payload[key];
    return typeof value === "string" || typeof value === "number" ? String(value) : "";
  }

  async function handleApproval(event: AgentTaskEvent, approved: boolean) {
    if (!task) return;
    const groupId = payloadText(event, "group_id");
    if (!groupId) return;
    if (approved) await backendApi.approveGroup?.(task.id, groupId);
    else await backendApi.rejectGroup?.(task.id, groupId, "用户拒绝本组变更");
    setHandledApprovalGroups((current) => [...new Set([...current, groupId])]);
  }

  async function confirmEventClarification(event: AgentTaskEvent) {
    if (!task || !backendApi.confirmClarification) return;
    const decisionId = payloadText(event, "decision_id");
    if (!decisionId) return;
    await backendApi.confirmClarification(task.id, decisionId);
    confirmedClarificationsRef.current.add(decisionId);
    setConfirmedClarifications((current) => [...new Set([...current, decisionId])]);
    setClarificationOpen(false);
  }

  async function decideHighRiskGate(
    gate: AgentGraphHumanGate,
    decision: "approve" | "reject",
  ) {
    if (
      !task
      || !backendApi.decideGraphGate
      || typeof graphCursor !== "number"
      || !gate.membership_hash
      || !gate.items?.length
    ) {
      throw new Error("审批证据不完整，请等待任务刷新后重试");
    }
    const findingIds = gate.items.map((item) => item.finding_id);
    const result = await backendApi.decideGraphGate(
      task.id,
      gate.id,
      decision,
      decision === "approve"
        ? "操作人通过聊天窗口同意高风险治理操作"
        : "操作人通过聊天窗口拒绝高风险治理操作",
      {
        approved_finding_ids: decision === "approve" ? findingIds : [],
        rejected_finding_ids: decision === "reject" ? findingIds : [],
        graph_cursor: graphCursor,
        membership_hash: gate.membership_hash,
      },
    );
    setHighRiskGates((current) => current.map((item) => (
      item.id === gate.id
        ? { ...item, status: result.status, actionable: false }
        : item
    )));
    return result.status;
  }

  async function submitMediumRiskGates(
    gates: AgentGraphHumanGate[],
    rejectedFindingIds: Set<string>,
  ) {
    if (
      !task
      || !backendApi.decideGraphGates
      || typeof graphCursor !== "number"
      || gates.some((gate) =>
        gate.actionable === false
        || !gate.membership_hash
        || !gate.items?.length
      )
    ) {
      throw new Error("中风险审核证据不完整，请等待任务刷新后重试");
    }
    const decisions = gates.map((gate) => {
      const approvedFindingIds: string[] = [];
      const rejectedIds: string[] = [];
      for (const item of gate.items ?? []) {
        if (rejectedFindingIds.has(item.finding_id)) {
          rejectedIds.push(item.finding_id);
        } else {
          approvedFindingIds.push(item.finding_id);
        }
      }
      return {
        gate_id: gate.id,
        decision: approvedFindingIds.length ? "approve" as const : "reject" as const,
        reason: "操作人通过聊天窗口完成中风险批量复核",
        approved_finding_ids: approvedFindingIds,
        rejected_finding_ids: rejectedIds,
        graph_cursor: graphCursor,
        membership_hash: gate.membership_hash!,
      };
    });
    const result = await backendApi.decideGraphGates(task.id, decisions);
    const statuses = Object.fromEntries(
      result.decisions.map((decision) => [decision.gate_id, decision.status]),
    );
    setMediumRiskGates((current) => current.map((gate) => (
      statuses[gate.id]
        ? { ...gate, status: statuses[gate.id], actionable: false }
        : gate
    )));
    return statuses;
  }

  const isCollecting = state === "collecting";
  const taskActive = Boolean(task && !terminalTaskStatuses.has(task.status));
  const composerLocked = Boolean(taskActive && !clarificationOpen);
  const taskBlocked = task?.status === "blocked_model_error";
  const taskFailed = task?.status === "failed";
  const taskTitle = taskFailed
    ? "任务处理失败"
    : task?.status === "completed"
      ? "任务已完成"
      : task?.status === "terminated"
        ? "任务已终止"
        : taskBlocked
          ? "Agent 任务已暂停"
          : "任务进行中";
  const identityConflicts = identityGate?.conflicts ?? [];

  return (
    <main className="page-shell conversation-create-page apple-page">
      <div className="conversation-page-actions">
        <h1 className="conversation-assistant-title">数据同步助手</h1>
        <button
          className={`conversation-reset-button${contextLimitReached ? " is-emphasized" : ""}`}
          type="button"
          aria-label="开启新对话"
          title={taskActive ? "当前任务结束或终止后才能开启新对话" : "永久删除当前聊天并开启新对话"}
          disabled={hydrating || newConversationLoading || taskActive}
          onClick={() => {
            setNewConversationError(undefined);
            setNewConversationOpen(true);
          }}
        >
          <MessageSquarePlus size={16} />
          <span>开启新对话</span>
        </button>
      </div>

      {newConversationError && (
        <Alert
          className="conversation-reset-error"
          type="error"
          showIcon
          message={newConversationError}
        />
      )}
      <div className="conversation-workspace has-task-status">
        <section className="conversation-surface" aria-label="新建对话">
        <div className="conversation-messages" aria-live="polite">
          {messages.map((message) => (
            <article
              className={`conversation-message ${message.role} ${message.kind ?? ""}`}
              aria-label={message.role === "assistant" ? "同步助手消息" : "你的消息"}
              key={message.id}
            >
              <span className="message-avatar">{message.role === "assistant" ? <Bot size={17} /> : <UserRound size={17} />}</span>
              <div>
                <strong>{message.role === "assistant" ? "同步助手" : "你"}</strong>
                <p>{message.text}</p>
              </div>
            </article>
          ))}
          {apiConnection && conversationId && !taskActive && (
            <ConversationApiConnectionCard
              connection={apiConnection}
              conversationId={conversationId}
              configure={backendApi.configureApiConnection}
              onChange={setApiConnection}
            />
          )}
          {confirmation && !taskActive && (
            <article className="conversation-card start-confirmation" aria-label="开始确认">
              <strong className="start-confirmation-title">开始同步前确认</strong>
              <dl className="start-confirmation-details">
                <div>
                  <dt>第三方对象</dt>
                  <dd>{confirmationSourceLabel(agentIntent)}</dd>
                </div>
                <div>
                  <dt>同步数据</dt>
                  <dd>{confirmationEntities(confirmation.entity_types)}</dd>
                </div>
              </dl>
              <button type="button" onClick={() => void startTask()}>确认开始同步</button>
              {targetBaselineDrift && (
                <button
                  type="button"
                  onClick={() => void startTask(true)}
                >
                  将当前文件作为新基线继续
                </button>
              )}
            </article>
          )}
          {task && (
            <article className={`conversation-card agent-progress${taskBlocked || taskFailed ? " blocked" : ""}`} aria-label="Agent 任务进度">
              <strong>{taskTitle}</strong>
              <p>当前阶段：{presentAgentPhase(task.phase)}</p>
              {identityGate && (
                <section className="conversation-identity-clarification">
                  {(
                    !backendApi.submitClarificationSelection
                    || !backendApi.confirmClarification
                    || typeof graphCursor !== "number"
                  ) ? (
                    <Alert
                      type="error"
                      showIcon
                      message="当前客户端缺少身份冲突处理能力，请刷新页面后重试。"
                    />
                  ) : (
                    identityConflicts.map((conflict, conflictIndex) => (
                      <IdentityConflictClarificationCard
                        key={conflict.clarification_id}
                        taskId={task.id}
                        gate={identityGate}
                        conflict={conflict}
                        conflictIndex={conflictIndex}
                        conflictCount={identityConflicts.length}
                        graphCursor={graphCursor}
                        api={{
                          submitClarificationSelection:
                            backendApi.submitClarificationSelection!,
                          confirmClarification: backendApi.confirmClarification!,
                        }}
                        onOptimisticSubmission={(
                          clarificationId,
                          submission: AgentClarificationSubmission | null,
                        ) => {
                          setIdentityGate((currentGate) => (
                            currentGate
                              ? {
                                  ...currentGate,
                                  conflicts: currentGate.conflicts?.map((item) => (
                                    item.clarification_id === clarificationId
                                      ? {
                                          ...item,
                                          status: submission ? "interpreted" : "pending",
                                          interpretation_zh:
                                            submission?.interpretation_zh ?? null,
                                          operator_submission: submission,
                                        }
                                      : item
                                  )),
                                }
                              : currentGate
                          ));
                        }}
                        onRefresh={async () => {
                          if (!backendApi.graph) return;
                          const progress = await backendApi.graph(task.id);
                          setGraphCursor(progress.graph_cursor);
                          setIdentityGate(
                            visibleIdentityGate(
                              progress,
                              confirmedClarificationsRef.current,
                            ),
                          );
                        }}
                        onConfirmed={(clarificationId) => {
                          confirmedClarificationsRef.current.add(clarificationId);
                          setConfirmedClarifications((current) => [
                            ...new Set([...current, clarificationId]),
                          ]);
                          setIdentityGate((currentGate) => {
                            if (!currentGate) return undefined;
                            const remaining = currentGate.conflicts?.filter(
                              (item) => item.clarification_id !== clarificationId,
                            );
                            return remaining?.length
                              ? { ...currentGate, conflicts: remaining }
                              : undefined;
                          });
                          setMessages((current) => [...current, {
                            id: messageId(),
                            role: "assistant",
                            text: "身份冲突选择已确认，Agent 正在继续处理。",
                          }]);
                        }}
                      />
                    ))
                  )}
                </section>
              )}
              {highRiskGates.map((gate) => (
                <ConversationRiskApprovalCard
                  gate={gate}
                  key={gate.id}
                  onDecide={decideHighRiskGate}
                />
              ))}
              {mediumRiskGates.length > 0 && (
                <ConversationMediumRiskReviewCard
                  gates={mediumRiskGates}
                  onSubmit={submitMediumRiskGates}
                />
              )}
              <div className="agent-event-list">
                {events
                  .filter((event) => !(
                    (
                      (highRiskGates.length > 0 || mediumRiskGates.length > 0)
                      && event.type === "approval_required"
                    )
                    || (
                      Boolean(identityGate)
                      && ["clarification_required", "clarification_decision_ready"].includes(
                        event.type,
                      )
                    )
                  ))
                  .slice(-6)
                  .map((event) => {
                  const groupId = payloadText(event, "group_id");
                  const decisionId = payloadText(event, "decision_id");
                  const approvalEvent = event.type === "approval_required" && groupId;
                  const decisionEvent = event.type === "clarification_decision_ready" && decisionId;
                  const presented = presentAgentEvent(event);
                  return (
                    <div className={`agent-event ${presented.tone}`} key={event.id}>
                      <span className="agent-event-dot" aria-hidden="true" />
                      <div className="agent-event-copy">
                        <strong>{presented.title}</strong>
                        <small>{presented.description}</small>
                        {presented.time && <time dateTime={event.created_at}>{presented.time}</time>}
                      </div>
                      {event.type === "clarification_required" && payloadText(event, "masked_evidence") && (
                        <small>证据：{payloadText(event, "masked_evidence")}</small>
                      )}
                      {approvalEvent && !handledApprovalGroups.includes(groupId) && (
                        <div className="agent-event-actions">
                          <button type="button" onClick={() => void handleApproval(event, true)}>同意本组</button>
                          <button type="button" onClick={() => void handleApproval(event, false)}>拒绝本组</button>
                        </div>
                      )}
                      {decisionEvent && !confirmedClarifications.includes(decisionId) && (
                        <div className="agent-event-actions">
                          <small>{payloadText(event, "summary")}</small>
                          <button type="button" onClick={() => void confirmEventClarification(event)}>确认解释</button>
                          <button type="button" onClick={() => setClarificationOpen(true)}>重新说明</button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              {terminationError && <Alert type="error" showIcon message={terminationError} />}
              {taskActive && <button type="button" disabled={terminationLoading} onClick={() => void terminateTask()}>终止任务</button>}
            </article>
          )}
          {state === "collecting" && <div className="assistant-thinking"><Spin size="small" /> 正在理解同步需求</div>}
        </div>

        <form className="conversation-composer" onSubmit={(event) => void sendMessage(event)}>
          <textarea
            aria-label="对账目标"
            placeholder={
              clarificationOpen
                ? "请说明当前身份冲突应选择哪个候选，或按希沃多余处理"
                : task && !taskActive
                  ? "继续描述下一次数据同步任务"
                  : "例如：只核对七年级的老师和学生"
            }
            rows={2}
            disabled={hydrating || isCollecting || composerLocked}
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />
          <button type="submit" aria-label="发送" title="发送" disabled={hydrating || !input.trim() || state === "collecting" || composerLocked}>
            <ArrowUp size={18} />
          </button>
        </form>
        </section>
        <TaskStatusRail
          stages={agentTaskStages}
          currentIndex={task ? taskStageIndex(task.phase) : -1}
          blocked={taskBlocked || taskFailed}
          terminationRequested={task?.status === "terminated"}
        />
      </div>
      <Modal
        rootClassName="apple-agent-modal"
        title="开启新对话？"
        open={newConversationOpen}
        okText="永久删除并开启"
        cancelText="保留当前对话"
        okButtonProps={{ danger: true }}
        confirmLoading={newConversationLoading}
        closable={!newConversationLoading}
        maskClosable={false}
        onOk={() => void confirmNewConversation()}
        onCancel={() => setNewConversationOpen(false)}
      >
        <p>聊天记录将永久删除，但数据同步任务、治理记录和报告不会被删除。</p>
      </Modal>
      <Modal
        rootClassName="apple-agent-modal"
        title="确认终止当前任务？"
        open={Boolean(terminationGate)}
        okText="确认终止"
        cancelText="继续执行"
        okButtonProps={{ danger: true }}
        confirmLoading={terminationLoading}
        closable={!terminationLoading}
        maskClosable={false}
        onOk={() => void decideTermination("approve")}
        onCancel={() => void decideTermination("reject")}
      >
        <p>终止后不会启动新的处理动作，当前原子操作会安全结束；已经验证成功的数据修改不会自动回退，系统将生成终止报告。</p>
      </Modal>
    </main>
  );
}

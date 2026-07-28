import { Alert, Modal, Spin } from "antd";
import {
  ArrowUp,
  Bot,
  MessageSquarePlus,
  MessageSquareText,
  UserRound,
} from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { agentApi as defaultAgentApi, type AgentConversationApi, type AgentGraphHumanGate, type AgentIntent, type AgentStartConfirmation, type AgentTask, type AgentTaskEvent } from "../../api/agent";
import { ApiError } from "../../api/client";
import { TASK_HISTORY_UPDATED_EVENT } from "../../data/taskHistory";
import { IdentityConflictEvidence } from "../../components/IdentityConflictEvidence";
import { TaskStatusRail } from "../../components/TaskStatusRail";
import { presentAgentEvent, presentAgentPhase } from "../agent-events/presentation";
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
  const [task, setTask] = useState<AgentTask>();
  const [events, setEvents] = useState<AgentTaskEvent[]>([]);
  const [eventCursor, setEventCursor] = useState<string>();
  const [clarificationOpen, setClarificationOpen] = useState(false);
  const [identityGate, setIdentityGate] = useState<AgentGraphHumanGate>();
  const [clarificationDecisionId, setClarificationDecisionId] = useState<string>();
  const [clarificationInterpretation, setClarificationInterpretation] = useState<string>();
  const [rewritingClarificationId, setRewritingClarificationId] = useState<string>();
  const [clarificationError, setClarificationError] = useState<string>();
  const [handledApprovalGroups, setHandledApprovalGroups] = useState<string[]>([]);
  const [confirmedClarifications, setConfirmedClarifications] = useState<string[]>([]);
  const [terminationGate, setTerminationGate] = useState<AgentGraphHumanGate>();
  const [highRiskGates, setHighRiskGates] = useState<AgentGraphHumanGate[]>([]);
  const [graphCursor, setGraphCursor] = useState<number>();
  const [terminationLoading, setTerminationLoading] = useState(false);
  const [terminationError, setTerminationError] = useState<string>();
  const [hydrating, setHydrating] = useState(true);
  const [newConversationOpen, setNewConversationOpen] = useState(false);
  const [newConversationLoading, setNewConversationLoading] = useState(false);
  const [newConversationError, setNewConversationError] = useState<string>();
  const [contextLimitReached, setContextLimitReached] = useState(false);

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
          const restoredTask = current.task ?? undefined;
          setConfirmation(restoredTask ? undefined : current.start_confirmation ?? undefined);
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
          const currentIdentityGate = graphProgress.human_gates.find(
            (gate) =>
              gate.kind === "identity_conflict"
              && gate.status === "pending",
          );
          setIdentityGate(currentIdentityGate);
          const currentConflict = currentIdentityGate?.conflicts?.find(
            (conflict) => ["pending", "interpreted"].includes(conflict.status),
          );
          if (!currentConflict) {
            setClarificationOpen(false);
            setClarificationDecisionId(undefined);
            setClarificationInterpretation(undefined);
            setRewritingClarificationId(undefined);
            setClarificationError(undefined);
          } else if (rewritingClarificationId !== currentConflict.clarification_id) {
            if (currentConflict.status === "interpreted") {
              setClarificationOpen(false);
              setClarificationDecisionId(currentConflict.clarification_id);
              setClarificationInterpretation(
                currentConflict.interpretation_zh ?? "模型已形成受限解释，请确认后继续。",
              );
            } else {
              setClarificationOpen(true);
              setClarificationDecisionId(undefined);
              setClarificationInterpretation(undefined);
            }
          }
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
  }, [backendApi, eventCursor, rewritingClarificationId, task]);

  useEffect(() => {
    setHighRiskGates([]);
    setGraphCursor(undefined);
    setIdentityGate(undefined);
    setClarificationDecisionId(undefined);
    setClarificationInterpretation(undefined);
    setRewritingClarificationId(undefined);
    setClarificationError(undefined);
  }, [task?.id]);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || hydrating || state === "collecting" || (task && !clarificationOpen)) return;
    setInput("");
    setState("collecting");
    setMessages((current) => [...current, { id: messageId(), role: "user", text: message }]);
    try {
      if (task && clarificationOpen && backendApi.clarify) {
        setClarificationError(undefined);
        const interpretation = await backendApi.clarify(task.id, message);
        setRewritingClarificationId(undefined);
        setClarificationInterpretation(interpretation.interpretation_zh);
        setClarificationDecisionId(
          interpretation.requires_second_confirmation
            ? interpretation.decision_id
            : undefined,
        );
        setClarificationOpen(!interpretation.requires_second_confirmation);
        setState("created");
        setMessages((current) => [...current, {
          id: messageId(),
          role: "assistant",
          text: interpretation.requires_second_confirmation
            ? "已生成受限解释，请核对冲突卡片并确认后继续。"
            : interpretation.interpretation_zh,
        }]);
        return;
      }
      const response = await backendApi.sendMessage(
        conversationId ?? await createConversation(),
        message,
      );
      setContextLimitReached(false);
      setAgentIntent(response.intent);
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: response.message,
      }]);
      if (response.start_confirmation) {
        setConfirmation(response.start_confirmation);
      }
      setState(response.start_confirmation ? "draft-ready" : "needs-input");
    } catch (error) {
      setContextLimitReached(
        error instanceof ApiError && error.code === "conversation_context_limit",
      );
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: error instanceof Error
          ? error.message
          : "没有理解这条要求，请换一种说法后重试。",
        kind: "error",
      }]);
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
      setTask(undefined);
      setEvents([]);
      setEventCursor(undefined);
      setClarificationOpen(false);
      setIdentityGate(undefined);
      setClarificationDecisionId(undefined);
      setClarificationInterpretation(undefined);
      setRewritingClarificationId(undefined);
      setClarificationError(undefined);
      setHandledApprovalGroups([]);
      setConfirmedClarifications([]);
      setTerminationGate(undefined);
      setHighRiskGates([]);
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

  async function startTask() {
    if (!confirmation || !conversationId || task) return;
    setState("submitting");
    try {
      const created = await backendApi.startTask(conversationId, {
        title: agentIntent?.title || confirmation.title,
        entity_types: confirmation.entity_types,
        source: agentIntent?.source,
        target: agentIntent?.target,
      }, sessionKey());
      setTask(created);
      setConfirmation(undefined);
      setState("created");
      window.dispatchEvent(new Event(TASK_HISTORY_UPDATED_EVENT));
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: "任务已开始，我会持续同步后端进度。普通输入已锁定。",
      }]);
    } catch (error) {
      setState("failed");
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: error instanceof ApiError && error.code === "school_lock_conflict"
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
    setConfirmedClarifications((current) => [...new Set([...current, decisionId])]);
    setClarificationOpen(false);
  }

  async function confirmIdentityClarification() {
    if (!task || !clarificationDecisionId || !backendApi.confirmClarification) return;
    setClarificationError(undefined);
    try {
      await backendApi.confirmClarification(task.id, clarificationDecisionId);
      setConfirmedClarifications((current) => [
        ...new Set([...current, clarificationDecisionId]),
      ]);
      setClarificationOpen(false);
      setClarificationDecisionId(undefined);
      setClarificationInterpretation(undefined);
      setRewritingClarificationId(undefined);
      setIdentityGate(undefined);
    } catch (error) {
      setClarificationError(
        error instanceof Error ? error.message : "身份冲突解释未确认，请重试。",
      );
    }
  }

  function rewriteIdentityClarification() {
    if (!clarificationDecisionId) return;
    setRewritingClarificationId(clarificationDecisionId);
    setClarificationDecisionId(undefined);
    setClarificationInterpretation(undefined);
    setClarificationError(undefined);
    setClarificationOpen(true);
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

  const isCollecting = state === "collecting";
  const taskActive = Boolean(task && !terminalTaskStatuses.has(task.status));
  const composerLocked = Boolean(task && !clarificationOpen);
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
  const currentIdentityConflictIndex = Math.max(
    identityConflicts.findIndex((conflict) =>
      ["pending", "interpreted"].includes(conflict.status),
    ),
    0,
  );
  const currentIdentityConflict = identityConflicts[currentIdentityConflictIndex];

  return (
    <main className="page-shell conversation-create-page apple-page">
      <header className="conversation-page-heading">
        <span className="page-heading-mark"><MessageSquareText size={20} /></span>
        <div>
          <h1>新建对话</h1>
          <p>当前学校 · 智能数据同步助手</p>
        </div>
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
      </header>

      {newConversationError && (
        <Alert
          className="conversation-reset-error"
          type="error"
          showIcon
          message={newConversationError}
        />
      )}
      <div className={`conversation-workspace${task ? " has-task-status" : ""}`}>
        <section className="conversation-surface" aria-label="新建对话">
        <div className="conversation-messages" aria-live="polite">
          {messages.map((message) => (
            <article className={`conversation-message ${message.role} ${message.kind ?? ""}`} key={message.id}>
              <span className="message-avatar">{message.role === "assistant" ? <Bot size={17} /> : <UserRound size={17} />}</span>
              <div>
                <strong>{message.role === "assistant" ? "同步助手" : "你"}</strong>
                <p>{message.text}</p>
              </div>
            </article>
          ))}
          {confirmation && !task && (
            <article className="conversation-card start-confirmation" aria-label="开始确认">
              <strong>开始同步前确认</strong>
              <p>{confirmation.summary}</p>
              <small>对象：{confirmation.entity_types.join("、")}</small>
              <button type="button" onClick={() => void startTask()}>确认开始同步</button>
            </article>
          )}
          {task && (
            <article className={`conversation-card agent-progress${taskBlocked || taskFailed ? " blocked" : ""}`} aria-label="Agent 任务进度">
              <strong>{taskTitle}</strong>
              <p>当前阶段：{presentAgentPhase(task.phase)}</p>
              {identityGate && (
                <section className="conversation-identity-clarification">
                  <header>
                    <strong>需要你判断一条身份冲突</strong>
                    <span>当前任务已暂停在此处</span>
                  </header>
                  {currentIdentityConflict ? (
                    <IdentityConflictEvidence
                      conflict={currentIdentityConflict}
                      index={currentIdentityConflictIndex}
                      total={identityConflicts.length}
                    />
                  ) : (
                    <Alert
                      type="error"
                      showIcon
                      message={
                        identityGate.unavailable_reason_zh
                        ?? "冲突明细不完整，不能要求你盲目判断。"
                      }
                    />
                  )}
                  {clarificationInterpretation && (
                    <Alert
                      type="info"
                      showIcon
                      message="待确认的模型解释"
                      description={clarificationInterpretation}
                    />
                  )}
                  {clarificationError && (
                    <Alert type="error" showIcon message={clarificationError} />
                  )}
                  {clarificationDecisionId ? (
                    <div className="conversation-identity-actions">
                      <button type="button" onClick={rewriteIdentityClarification}>
                        重新说明
                      </button>
                      <button
                        type="button"
                        onClick={() => void confirmIdentityClarification()}
                      >
                        确认模型解释
                      </button>
                    </div>
                  ) : (
                    <small>
                      请直接在下方输入框说明当前记录应采用哪个候选，或明确按“希沃多余”处理。
                    </small>
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
              <div className="agent-event-list">
                {events
                  .filter((event) => !(
                    (highRiskGates.length > 0 && event.type === "approval_required")
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
        {task && (
          <TaskStatusRail
            stages={agentTaskStages}
            currentIndex={taskStageIndex(task.phase)}
            blocked={taskBlocked || taskFailed}
            terminationRequested={task.status === "terminated"}
          />
        )}
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

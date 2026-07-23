import { Spin } from "antd";
import { ArrowUp, Bot, MessageSquareText, UserRound } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { agentApi as defaultAgentApi, type AgentConversationApi, type AgentIntent, type AgentStartConfirmation, type AgentTask, type AgentTaskEvent } from "../../api/agent";
import { createEmptyTaskIntentDraft } from "./assistant";
import { clearTaskIntentDraft } from "./draftHandoff";
import type {
  ConversationMessage,
  ConversationState,
  TaskCreationAssistant,
  TaskIntentDraft,
} from "./types";
import { isAssistantResponse, isTaskIntentDraft, isTaskIntentReady } from "./types";

const initialMessages: ConversationMessage[] = [{
  id: "assistant-welcome",
  role: "assistant",
  text: "你好，我是智能数据同步助手。告诉我希望同步的范围和对象。",
}];

function messageId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function sessionKey() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function ConversationCreatePage({
  assistant,
  agentApi,
}: {
  assistant?: TaskCreationAssistant;
  agentApi?: AgentConversationApi;
}) {
  const [draft, setDraft] = useState<TaskIntentDraft>(() => createEmptyTaskIntentDraft());
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
  const [handledApprovalGroups, setHandledApprovalGroups] = useState<string[]>([]);
  const [confirmedClarifications, setConfirmedClarifications] = useState<string[]>([]);

  const backendApi = agentApi ?? defaultAgentApi;

  useEffect(() => {
    clearTaskIntentDraft();
    if (assistant) return;
    void backendApi.createConversation().then((conversation) => setConversationId(conversation.id)).catch(() => {
      setState("failed");
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: "对话服务暂时不可用，请稍后重试。",
        kind: "error",
      }]);
    });
  }, [agentApi, assistant, backendApi]);

  useEffect(() => {
    if (!task || !backendApi.events) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const page = await backendApi.events(task.id, eventCursor);
        if (cancelled) return;
        setEventCursor(page.cursor);
        setEvents((current) => {
          const known = new Set(current.map((item) => item.id));
          return [...current, ...page.events.filter((item) => !known.has(item.id))];
        });
        const latest = page.events.at(-1);
        if (latest?.type === "clarification_required") setClarificationOpen(true);
        if (["completed", "terminated", "failed"].includes(latest?.status ?? "")) setState("created");
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
  }, [backendApi, eventCursor, task]);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || state === "collecting" || (task && !clarificationOpen)) return;
    setInput("");
    setState("collecting");
    setMessages((current) => [...current, { id: messageId(), role: "user", text: message }]);
    try {
      if (assistant) {
        const response = await assistant.respond({ draft, message });
        if (!isAssistantResponse(response)) throw new Error("Invalid assistant response");
        const nextDraft = { ...draft, ...response.patch };
        if (!isTaskIntentDraft(nextDraft)) throw new Error("Invalid assistant draft");
        setDraft(nextDraft);
        setMessages((current) => [...current, {
          id: messageId(),
          role: "assistant",
          text: response.message,
          kind: response.kind,
        }]);
        setState(isTaskIntentReady(nextDraft) ? "draft-ready" : "needs-input");
        return;
      }
      if (task && clarificationOpen && backendApi.clarify) {
        await backendApi.clarify(task.id, message);
        setClarificationOpen(false);
        setState("created");
        setMessages((current) => [...current, {
          id: messageId(),
          role: "assistant",
          text: "已提交澄清，等待后端生成结构化决策确认。",
        }]);
        return;
      }
      const response = await backendApi.sendMessage(
        conversationId ?? await createConversation(),
        message,
      );
      const nextDraft: TaskIntentDraft = {
        ...draft,
        title: response.intent.title,
        entityTypes: response.intent.entity_types.map((type) => type === "department" ? "organization_unit" : type),
      };
      setAgentIntent(response.intent);
      setDraft(nextDraft);
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: response.message,
      }]);
      if (response.start_confirmation) {
        setConfirmation(response.start_confirmation);
      }
      setState(response.start_confirmation ? "draft-ready" : "needs-input");
    } catch {
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: "没有理解这条要求，请换一种说法后重试。",
        kind: "error",
      }]);
      setState("failed");
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
        title: draft.title || confirmation.title,
        entity_types: confirmation.entity_types,
        source: agentIntent?.source,
        target: agentIntent?.target,
      }, sessionKey());
      setTask(created);
      setConfirmation(undefined);
      setState("created");
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: "任务已开始，我会持续同步后端进度。普通输入已锁定。",
      }]);
    } catch {
      setState("failed");
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: "任务启动失败，现有需求仍然保留，可以重试。",
        kind: "error",
      }]);
    }
  }

  async function terminateTask() {
    if (!task) return;
    await backendApi.terminate(task.id);
  }

  function eventText(event: AgentTaskEvent) {
    const labels: Record<string, string> = {
      ingest_and_normalize: "数据接入",
      build_identity_work: "身份索引",
      analyze_batches: "Agent 分析",
      aggregate_risk_and_approvals: "风险审批",
      execute_and_verify: "治理执行",
      generate_report: "报告生成",
      report_restore: "回滚报告",
      terminal: "任务结束",
    };
    return labels[event.phase ?? ""] ?? event.type;
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

  async function confirmClarification(event: AgentTaskEvent) {
    if (!task || !backendApi.confirmClarification) return;
    const decisionId = payloadText(event, "decision_id");
    if (!decisionId) return;
    await backendApi.confirmClarification(task.id, decisionId);
    setConfirmedClarifications((current) => [...new Set([...current, decisionId])]);
    setClarificationOpen(false);
  }

  const isCollecting = state === "collecting";
  const taskActive = Boolean(task && !["completed", "terminated", "failed"].includes(task.status));

  return (
    <main className="page-shell conversation-create-page">
      <header className="conversation-page-heading">
        <span className="page-heading-mark"><MessageSquareText size={20} /></span>
        <div>
          <h1>新建对话</h1>
          <p>当前学校 · 智能数据同步助手</p>
        </div>
      </header>

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
          {confirmation && (
            <article className="conversation-card start-confirmation" aria-label="开始确认">
              <strong>开始同步前确认</strong>
              <p>{confirmation.summary}</p>
              <small>对象：{confirmation.entity_types.join("、")}</small>
              <button type="button" onClick={() => void startTask()}>确认开始同步</button>
            </article>
          )}
          {task && (
            <article className="conversation-card agent-progress" aria-label="Agent 任务进度">
              <strong>任务进行中</strong>
              <p>当前阶段：{eventText({ id: "phase", cursor: "", type: "phase", phase: task.phase, payload: {}, created_at: "" })}</p>
              <div className="agent-event-list">
                {events.slice(-6).map((event) => {
                  const groupId = payloadText(event, "group_id");
                  const decisionId = payloadText(event, "decision_id");
                  const approvalEvent = event.type === "approval_required" && groupId;
                  const decisionEvent = event.type === "clarification_decision_ready" && decisionId;
                  return (
                    <div className="agent-event" key={event.id}>
                      <span>{event.type === "clarification_required" ? "发现身份冲突，需要补充说明" : eventText(event)}</span>
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
                          <button type="button" onClick={() => void confirmClarification(event)}>确认解释</button>
                          <button type="button" onClick={() => setClarificationOpen(true)}>重新说明</button>
                        </div>
                      )}
                      {event.type === "model_retry_exhausted" && <small className="agent-event-error">模型重试失败，请终止任务后检查报告。</small>}
                      {(event.type === "report_ready" || event.type === "report.completed") && (
                        <small>报告已生成：成功 {payloadText(event, "succeeded") || "0"}，失败 {payloadText(event, "failed") || "0"}</small>
                      )}
                    </div>
                  );
                })}
              </div>
              {taskActive && <button type="button" onClick={() => void terminateTask()}>终止任务</button>}
            </article>
          )}
          {state === "collecting" && <div className="assistant-thinking"><Spin size="small" /> 正在理解同步需求</div>}
        </div>

        <form className="conversation-composer" onSubmit={(event) => void sendMessage(event)}>
          <textarea
            aria-label="对账目标"
            placeholder="例如：只核对七年级的老师和学生"
            rows={2}
            disabled={isCollecting || Boolean(taskActive && !clarificationOpen)}
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />
          <button type="submit" aria-label="发送" title="发送" disabled={!input.trim() || state === "collecting" || Boolean(taskActive && !clarificationOpen)}>
            <ArrowUp size={18} />
          </button>
        </form>
      </section>
    </main>
  );
}

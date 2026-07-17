import { Alert, Button, Checkbox, Spin } from "antd";
import {
  ArrowUp,
  Bot,
  Check,
  FileSpreadsheet,
  Paperclip,
  Sparkles,
  UserRound,
} from "lucide-react";
import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { entityLabels } from "../../data/demoDifferences";
import type { EntityType } from "../../types/domain";
import { createInitialDraft, deterministicTaskAssistant } from "./assistant";
import { summarizeCsv } from "./csvSummary";
import { createTaskFromDraft } from "./taskCreationService";
import type { ConversationMessage, ConversationState, DraftAttachment, TaskDraft } from "./types";
import { isDraftReady } from "./types";

const entityTypes: EntityType[] = ["organization_unit", "class", "teacher", "student"];

const initialMessages: ConversationMessage[] = [{
  id: "assistant-welcome",
  role: "assistant",
  text: "你好，我来整理本次对账任务。告诉我核对范围和人员类型，或者先补充两份演示数据。",
}];

function messageId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function sessionKey() {
  return globalThis.crypto?.randomUUID?.() ?? messageId();
}

function AttachmentPicker({
  label,
  inputLabel,
  tone,
  attachment,
  onChange,
}: {
  label: string;
  inputLabel: string;
  tone: "source" | "target";
  attachment?: DraftAttachment;
  onChange: (file: File) => void;
}) {
  return (
    <label className={`conversation-attachment attachment-${tone}`}>
      <input
        accept=".csv,text/csv"
        aria-label={inputLabel}
        type="file"
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          const file = event.target.files?.[0];
          if (file) onChange(file);
        }}
      />
      <span className="attachment-icon">{attachment?.summary ? <Check size={16} /> : attachment ? <Spin size="small" /> : <Paperclip size={16} />}</span>
      <span className="attachment-copy">
        <strong>{attachment?.file.name ?? label}</strong>
        <small>{attachment?.error ?? (attachment?.summary ? `${attachment.summary.total} 条数据` : "选择 CSV")}</small>
      </span>
      {attachment?.summary && <FileSpreadsheet size={16} />}
    </label>
  );
}

export function TaskCreatePage() {
  const navigate = useNavigate();
  const [draft, setDraft] = useState<TaskDraft>(() => createInitialDraft());
  const [messages, setMessages] = useState<ConversationMessage[]>(initialMessages);
  const [input, setInput] = useState("");
  const [state, setState] = useState<ConversationState>("idle");
  const [submitError, setSubmitError] = useState<string>();
  const idempotencyKey = useRef(sessionKey());

  async function prepareFile(role: "source" | "target", file: File) {
    setDraft((current) => ({ ...current, [role]: { file } }));
    try {
      const summary = await summarizeCsv(file);
      setDraft((current) => ({ ...current, [role]: { file, summary } }));
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: `${role === "source" ? "三方系统" : "希沃魔方"}数据已读取，共 ${summary.total} 条。`,
      }]);
    } catch (error) {
      setDraft((current) => ({
        ...current,
        [role]: { file, error: error instanceof Error ? error.message : "文件读取失败" },
      }));
    }
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || state === "collecting") return;
    setInput("");
    setState("collecting");
    setMessages((current) => [...current, { id: messageId(), role: "user", text: message }]);
    try {
      const response = await deterministicTaskAssistant.respond({ draft, message });
      const nextDraft = { ...draft, ...response.patch };
      setDraft(nextDraft);
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: response.message,
        kind: response.kind,
      }]);
      setState(isDraftReady(nextDraft) ? "draft-ready" : "needs-input");
    } catch {
      setMessages((current) => [...current, { id: messageId(), role: "assistant", text: "没有理解这条要求，请换一种说法或直接编辑任务草案。", kind: "error" }]);
      setState("failed");
    }
  }

  function toggleType(entityType: EntityType, checked: boolean) {
    setDraft((current) => ({
      ...current,
      entityTypes: checked
        ? [...new Set([...current.entityTypes, entityType])]
        : current.entityTypes.filter((type) => type !== entityType),
    }));
  }

  async function createTask() {
    if (!isDraftReady(draft) || state === "submitting") return;
    setState("submitting");
    setSubmitError(undefined);
    try {
      const task = await createTaskFromDraft(draft, idempotencyKey.current);
      setState("created");
      navigate(`/tasks/${task.id}`);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "任务创建失败，请稍后重试");
      setState("failed");
    }
  }

  const ready = isDraftReady(draft);

  return (
    <main className="page-shell assistant-create-page">
      <header className="assistant-page-heading">
        <span className="assistant-heading-icon"><Sparkles size={20} /></span>
        <div><h1>和 AI 一起新建对账</h1><p>当前学校 · 演示数据模式</p></div>
        <span className="assistant-mode"><span />任务助手</span>
      </header>

      <div className="assistant-create-layout">
        <section className="conversation-workspace" aria-label="新建对账对话">
          <div className="conversation-messages" aria-live="polite">
            {messages.map((message) => (
              <article className={`conversation-message ${message.role} ${message.kind ?? ""}`} key={message.id}>
                <span className="message-avatar">{message.role === "assistant" ? <Bot size={17} /> : <UserRound size={17} />}</span>
                <div><strong>{message.role === "assistant" ? "任务助手" : "你"}</strong><p>{message.text}</p></div>
              </article>
            ))}
            {state === "collecting" && <div className="assistant-thinking"><Spin size="small" /> 正在整理任务草案</div>}
          </div>

          <div className="conversation-compose-area">
            <div className="conversation-attachments" aria-label="任务数据">
              <AttachmentPicker label="三方系统数据" inputLabel="选择三方系统 CSV" tone="source" attachment={draft.source} onChange={(file) => void prepareFile("source", file)} />
              <AttachmentPicker label="希沃魔方数据" inputLabel="选择希沃魔方 CSV" tone="target" attachment={draft.target} onChange={(file) => void prepareFile("target", file)} />
            </div>
            <form className="conversation-composer" onSubmit={(event) => void sendMessage(event)}>
              <textarea
                aria-label="对账要求"
                placeholder="例如：只核对七年级的老师和学生"
                rows={2}
                value={input}
                onChange={(event) => setInput(event.target.value)}
              />
              <button type="submit" aria-label="发送" title="发送" disabled={!input.trim() || state === "collecting"}><ArrowUp size={18} /></button>
            </form>
          </div>
        </section>

        <aside className="task-draft-panel" aria-label="任务草案">
          <div className="draft-heading"><span><Sparkles size={16} /></span><div><h2>任务草案</h2><p>{ready ? "信息完整，等待确认" : "继续补充任务信息"}</p></div></div>

          <label className="draft-field"><span>任务名称</span><input value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} /></label>
          <label className="draft-field"><span>核对范围</span><input value={draft.scopeLabel} onChange={(event) => setDraft((current) => ({ ...current, scopeLabel: event.target.value }))} /></label>

          <fieldset className="draft-fieldset">
            <legend>处理模式</legend>
            <div className="draft-segmented">
              <button className={draft.snapshotMode === "full" ? "active" : ""} type="button" onClick={() => setDraft((current) => ({ ...current, snapshotMode: "full" }))}>全量对账</button>
              <button className={draft.snapshotMode === "partial" ? "active" : ""} type="button" onClick={() => setDraft((current) => ({ ...current, snapshotMode: "partial" }))}>指定范围</button>
            </div>
          </fieldset>

          <fieldset className="draft-fieldset entity-checks">
            <legend>实体类型</legend>
            <div className="draft-entity-grid">
              {entityTypes.map((entityType) => (
                <Checkbox
                  key={entityType}
                  aria-label={entityLabels[entityType]}
                  checked={draft.entityTypes.includes(entityType)}
                  onChange={(event) => toggleType(entityType, event.target.checked)}
                >{entityLabels[entityType]}</Checkbox>
              ))}
            </div>
            <button className="text-button" type="button" onClick={() => setDraft((current) => ({ ...current, entityTypes: [] }))}>清空选择</button>
          </fieldset>

          <div className="draft-data-summary">
            <span>数据状态</span>
            <div><strong>三方系统</strong><small>{draft.source?.summary ? `${draft.source.summary.total} 条` : "待补充"}</small></div>
            <div><strong>希沃魔方</strong><small>{draft.target?.summary ? `${draft.target.summary.total} 条` : "待补充"}</small></div>
          </div>

          {submitError && <Alert className="draft-error" type="error" showIcon message={submitError} />}
          <Button className="draft-create-button" type="primary" size="large" loading={state === "submitting"} disabled={!ready || state === "submitting"} onClick={() => void createTask()}>
            创建对账
          </Button>
          <p className="draft-footnote">创建后进入实体解析与差异检测，不会直接修改数据。</p>
        </aside>
      </div>
    </main>
  );
}

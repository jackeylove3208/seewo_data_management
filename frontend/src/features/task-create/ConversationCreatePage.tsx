import { Button, Checkbox, Spin } from "antd";
import { ArrowRight, ArrowUp, Bot, MessageSquareText, Sparkles, UserRound } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { entityLabels } from "../../data/demoDifferences";
import type { EntityType } from "../../types/domain";
import { createEmptyTaskIntentDraft, deterministicTaskAssistant } from "./assistant";
import { clearTaskIntentDraft, saveTaskIntentDraft } from "./draftHandoff";
import type {
  ConversationMessage,
  ConversationState,
  TaskCreationAssistant,
  TaskIntentDraft,
} from "./types";
import { isAssistantResponse, isTaskIntentDraft, isTaskIntentReady } from "./types";

const entityTypes: EntityType[] = ["organization_unit", "class", "teacher", "student"];

const initialMessages: ConversationMessage[] = [{
  id: "assistant-welcome",
  role: "assistant",
  text: "你好，我来整理本次对账目标。告诉我核对范围和实体类型。",
}];

function messageId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function ConversationCreatePage({
  assistant = deterministicTaskAssistant,
}: {
  assistant?: TaskCreationAssistant;
}) {
  const navigate = useNavigate();
  const [draft, setDraft] = useState<TaskIntentDraft>(() => createEmptyTaskIntentDraft());
  const [messages, setMessages] = useState<ConversationMessage[]>(initialMessages);
  const [input, setInput] = useState("");
  const [state, setState] = useState<ConversationState>("idle");

  useEffect(() => {
    clearTaskIntentDraft();
  }, []);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || state === "collecting") return;
    setInput("");
    setState("collecting");
    setMessages((current) => [...current, { id: messageId(), role: "user", text: message }]);
    try {
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
    } catch {
      setMessages((current) => [...current, {
        id: messageId(),
        role: "assistant",
        text: "没有理解这条要求，请换一种说法或直接编辑任务草案。",
        kind: "error",
      }]);
      setState("failed");
    }
  }

  function updateDraft(patch: Partial<TaskIntentDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  function toggleType(entityType: EntityType, checked: boolean) {
    updateDraft({
      entityTypes: checked
        ? [...new Set([...draft.entityTypes, entityType])]
        : draft.entityTypes.filter((type) => type !== entityType),
    });
  }

  function continueToSync() {
    if (!saveTaskIntentDraft(draft)) return;
    navigate("/tasks/new");
  }

  const ready = isTaskIntentReady(draft);
  const isCollecting = state === "collecting";

  return (
    <main className="page-shell conversation-create-page">
      <header className="conversation-page-heading">
        <span className="page-heading-mark"><MessageSquareText size={20} /></span>
        <div>
          <h1>新建对话</h1>
          <p>当前学校 · 对账任务助手</p>
        </div>
        <span className="conversation-status"><span />任务意图</span>
      </header>

      <section className="conversation-surface" aria-label="新建对话">
        <div className="conversation-messages" aria-live="polite">
          {messages.map((message) => (
            <article className={`conversation-message ${message.role} ${message.kind ?? ""}`} key={message.id}>
              <span className="message-avatar">{message.role === "assistant" ? <Bot size={17} /> : <UserRound size={17} />}</span>
              <div>
                <strong>{message.role === "assistant" ? "任务助手" : "你"}</strong>
                <p>{message.text}</p>
              </div>
            </article>
          ))}
          {state === "collecting" && <div className="assistant-thinking"><Spin size="small" /> 正在整理任务草案</div>}
        </div>

        <form className="conversation-composer" onSubmit={(event) => void sendMessage(event)}>
          <textarea
            aria-label="对账目标"
            placeholder="例如：只核对七年级的老师和学生"
            rows={2}
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />
          <button type="submit" aria-label="发送" title="发送" disabled={!input.trim() || state === "collecting"}>
            <ArrowUp size={18} />
          </button>
        </form>
      </section>

      <section className="intent-draft-section" role="region" aria-label="任务草案">
        <div className="intent-draft-heading">
          <span><Sparkles size={17} /></span>
          <div>
            <h2>任务草案</h2>
            <p>{ready ? "信息完整" : "仍有必填信息未完成"}</p>
          </div>
        </div>

        <div className="intent-fields-grid">
          <label className="draft-field">
            <span>任务名称</span>
            <input aria-label="任务名称" disabled={isCollecting} value={draft.title} onChange={(event) => updateDraft({ title: event.target.value })} />
          </label>
          <label className="draft-field">
            <span>核对范围</span>
            <input aria-label="核对范围" disabled={isCollecting} value={draft.scopeLabel} onChange={(event) => updateDraft({ scopeLabel: event.target.value })} />
          </label>

          <fieldset className="draft-fieldset">
            <legend>处理模式</legend>
            <div className="draft-segmented">
              <button className={draft.snapshotMode === "full" ? "active" : ""} type="button" aria-pressed={draft.snapshotMode === "full"} disabled={isCollecting} onClick={() => updateDraft({ snapshotMode: "full" })}>全量对账</button>
              <button className={draft.snapshotMode === "partial" ? "active" : ""} type="button" aria-pressed={draft.snapshotMode === "partial"} disabled={isCollecting} onClick={() => updateDraft({ snapshotMode: "partial" })}>指定范围</button>
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
                  disabled={isCollecting}
                  onChange={(event) => toggleType(entityType, event.target.checked)}
                >{entityLabels[entityType]}</Checkbox>
              ))}
            </div>
            <button className="text-button" type="button" disabled={isCollecting} onClick={() => updateDraft({ entityTypes: [] })}>清空选择</button>
          </fieldset>
        </div>

        <div className="intent-draft-action">
          <span>{ready ? "草案可以进入数据同步" : "完成任务名称、范围和实体类型后继续"}</span>
          <Button type="primary" size="large" disabled={!ready || isCollecting} icon={<ArrowRight size={17} />} onClick={continueToSync}>
            继续外部数据同步
          </Button>
        </div>
      </section>
    </main>
  );
}

import { Spin } from "antd";
import { ArrowUp, Bot, MessageSquareText, UserRound } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { createEmptyTaskIntentDraft, deterministicTaskAssistant } from "./assistant";
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

export function ConversationCreatePage({
  assistant = deterministicTaskAssistant,
}: {
  assistant?: TaskCreationAssistant;
}) {
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
        text: "没有理解这条要求，请换一种说法后重试。",
        kind: "error",
      }]);
      setState("failed");
    }
  }

  const isCollecting = state === "collecting";

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
          {state === "collecting" && <div className="assistant-thinking"><Spin size="small" /> 正在理解同步需求</div>}
        </div>

        <form className="conversation-composer" onSubmit={(event) => void sendMessage(event)}>
          <textarea
            aria-label="对账目标"
            placeholder="例如：只核对七年级的老师和学生"
            rows={2}
            disabled={isCollecting}
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />
          <button type="submit" aria-label="发送" title="发送" disabled={!input.trim() || state === "collecting"}>
            <ArrowUp size={18} />
          </button>
        </form>
      </section>
    </main>
  );
}

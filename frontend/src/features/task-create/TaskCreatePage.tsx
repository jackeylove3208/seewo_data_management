import { Alert, Button, Checkbox, Spin } from "antd";
import {
  Check,
  CloudCog,
  FileSpreadsheet,
  FileUp,
  Paperclip,
} from "lucide-react";
import { useRef, useState, type ChangeEvent } from "react";
import { useNavigate } from "react-router-dom";

import { entityLabels } from "../../data/demoDifferences";
import type { EntityType } from "../../types/domain";
import { createInitialDraft } from "./assistant";
import { summarizeCsv } from "./csvSummary";
import { createTaskFromDraft } from "./taskCreationService";
import type { DraftAttachment, TaskDraft } from "./types";
import { isDraftReady } from "./types";

const entityTypes: EntityType[] = ["organization_unit", "class", "teacher", "student"];

type SubmissionState = "idle" | "submitting" | "failed" | "created";

function sessionKey() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
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
    <label className={`sync-attachment attachment-${tone}`}>
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
  const [syncMethod, setSyncMethod] = useState<"manual" | null>(null);
  const [draft, setDraft] = useState<TaskDraft>(() => createInitialDraft());
  const [submissionState, setSubmissionState] = useState<SubmissionState>("idle");
  const [submitError, setSubmitError] = useState<string>();
  const idempotencyKey = useRef(sessionKey());
  const fileRequestTokens = useRef({ source: 0, target: 0 });

  async function prepareFile(role: "source" | "target", file: File) {
    const requestToken = ++fileRequestTokens.current[role];
    setDraft((current) => ({ ...current, [role]: { file } }));
    try {
      const summary = await summarizeCsv(file);
      if (fileRequestTokens.current[role] !== requestToken) return;
      setDraft((current) => ({ ...current, [role]: { file, summary } }));
    } catch (error) {
      if (fileRequestTokens.current[role] !== requestToken) return;
      setDraft((current) => ({
        ...current,
        [role]: { file, error: error instanceof Error ? error.message : "文件读取失败" },
      }));
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
    if (!isDraftReady(draft) || submissionState === "submitting") return;
    setSubmissionState("submitting");
    setSubmitError(undefined);
    try {
      const task = await createTaskFromDraft(draft, idempotencyKey.current);
      setSubmissionState("created");
      navigate(`/tasks/${task.id}`);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "任务创建失败，请稍后重试");
      setSubmissionState("failed");
    }
  }

  const ready = isDraftReady(draft);

  return (
    <main className="page-shell external-sync-page">
      <header className="sync-page-heading">
        <div>
          <h1>外部数据同步</h1>
          <p>通过手动文件同步创建对账任务，后续处理流程保持不变。</p>
        </div>
      </header>

      <section className="sync-methods" aria-labelledby="sync-method-title">
        <div className="section-title-row">
          <div>
            <h2 id="sync-method-title">选择同步方式</h2>
            <p>先选择数据进入方式，再配置本次同步范围。</p>
          </div>
        </div>
        <div className="sync-method-grid">
          <button
            className={syncMethod === "manual" ? "sync-method active" : "sync-method"}
            type="button"
            aria-label="手动同步"
            aria-pressed={syncMethod === "manual"}
            onClick={() => setSyncMethod("manual")}
          >
            <FileUp size={20} />
            <span><strong>手动同步</strong><small>上传三方系统与希沃魔方 CSV</small></span>
          </button>
          <button className="sync-method" type="button" aria-label="系统自动同步，暂未开放" disabled>
            <CloudCog size={20} />
            <span><strong>系统自动同步</strong><small>暂未开放</small></span>
          </button>
        </div>
      </section>

      {syncMethod === "manual" && (
        <section className="manual-sync-form" aria-label="手动同步配置">
          <div className="sync-attachments" aria-label="任务数据">
            <AttachmentPicker label="三方系统数据" inputLabel="选择三方系统 CSV" tone="source" attachment={draft.source} onChange={(file) => void prepareFile("source", file)} />
            <AttachmentPicker label="希沃魔方数据" inputLabel="选择希沃魔方 CSV" tone="target" attachment={draft.target} onChange={(file) => void prepareFile("target", file)} />
          </div>

          <div className="sync-settings-grid">
            <label className="draft-field">
              <span>任务名称</span>
              <input aria-label="同步任务名称" value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} />
            </label>
            <label className="draft-field">
              <span>核对范围</span>
              <input aria-label="核对范围" value={draft.scopeLabel} onChange={(event) => setDraft((current) => ({ ...current, scopeLabel: event.target.value }))} />
            </label>

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
          </div>

          <div className="draft-data-summary">
            <span>数据状态</span>
            <div><strong>三方系统</strong><small>{draft.source?.summary ? `${draft.source.summary.total} 条` : "待补充"}</small></div>
            <div><strong>希沃魔方</strong><small>{draft.target?.summary ? `${draft.target.summary.total} 条` : "待补充"}</small></div>
          </div>

          {submitError && <Alert className="draft-error" type="error" showIcon message={submitError} />}
          <Button
            className="sync-start-button"
            type="primary"
            size="large"
            loading={submissionState === "submitting"}
            disabled={!ready || submissionState === "submitting"}
            onClick={() => void createTask()}
          >
            开始同步
          </Button>
          <p className="draft-footnote">创建后进入实体解析与差异检测，不会直接修改数据。</p>
        </section>
      )}
    </main>
  );
}

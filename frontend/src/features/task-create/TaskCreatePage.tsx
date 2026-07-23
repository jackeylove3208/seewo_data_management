import { Alert, Button, Checkbox, Spin } from "antd";
import { Check, FileSpreadsheet, FileUp, Paperclip, RefreshCw } from "lucide-react";
import { useRef, useState, type ChangeEvent } from "react";
import { useNavigate } from "react-router-dom";

import { agentApi as defaultAgentApi, type AgentConnectorSelection, type AgentEntityType, type AgentManualTaskApi } from "../../api/agent";
import { ingestionApi } from "../../api/ingestion";
import { summarizeCsv, type CsvSummary } from "./csvSummary";

type ConnectorKind = AgentConnectorSelection["kind"];
type ConnectorDraft = {
  kind: ConnectorKind;
  file?: File;
  summary?: CsvSummary;
  configurationId?: string;
  error?: string;
};

interface ManualAgentDraft {
  title: string;
  entityTypes: AgentEntityType[];
  source?: ConnectorDraft;
  target?: ConnectorDraft;
}

type SubmissionState = "idle" | "submitting" | "failed" | "created";

const entityTypes: AgentEntityType[] = ["department", "student", "teacher"];
const entityLabels: Record<AgentEntityType, string> = {
  department: "部门",
  student: "学生",
  teacher: "教师",
};

function sessionKey() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function readyConnector(connector?: ConnectorDraft) {
  if (!connector || connector.error) return false;
  return connector.kind === "csv" ? Boolean(connector.file && connector.summary) : Boolean(connector.configurationId?.trim());
}

function AttachmentPicker({
  label,
  inputLabel,
  tone,
  connector,
  disabled,
  onChange,
}: {
  label: string;
  inputLabel: string;
  tone: "source" | "target";
  connector?: ConnectorDraft;
  disabled?: boolean;
  onChange: (file: File) => void;
}) {
  const statusId = `agent-${tone}-file-status`;
  return (
    <label className={`sync-attachment attachment-${tone}`}>
      <input
        accept=".csv,text/csv"
        aria-label={inputLabel}
        aria-describedby={statusId}
        disabled={disabled}
        type="file"
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          const file = event.target.files?.[0];
          if (file) onChange(file);
        }}
      />
      <span className="attachment-icon">{connector?.summary ? <Check size={16} /> : connector?.file ? <Spin size="small" /> : <Paperclip size={16} />}</span>
      <span className="attachment-copy">
        <strong>{connector?.file?.name ?? label}</strong>
        <small id={statusId} role={connector?.error ? "alert" : "status"}>
          {connector?.error ?? (connector?.summary ? `${connector.summary.total} 条数据` : "选择 CSV")}
        </small>
      </span>
      {connector?.summary && <FileSpreadsheet size={16} />}
    </label>
  );
}

export function TaskCreatePage({
  api = defaultAgentApi,
}: {
  api?: AgentManualTaskApi;
}) {
  const navigate = useNavigate();
  const [syncMethod, setSyncMethod] = useState<"manual" | null>(null);
  const [draft, setDraft] = useState<ManualAgentDraft>({
    title: "全校组织数据同步",
    entityTypes: [...entityTypes],
    source: { kind: "csv" },
    target: { kind: "csv" },
  });
  const [submissionState, setSubmissionState] = useState<SubmissionState>("idle");
  const [submitError, setSubmitError] = useState<string>();
  const fileRequestTokens = useRef({ source: 0, target: 0 });

  async function prepareFile(role: "source" | "target", file: File) {
    const requestToken = ++fileRequestTokens.current[role];
    setDraft((current) => ({ ...current, [role]: { kind: "csv", file } }));
    try {
      const summary = await summarizeCsv(file);
      if (fileRequestTokens.current[role] !== requestToken) return;
      setDraft((current) => ({ ...current, [role]: { kind: "csv", file, summary } }));
    } catch (error) {
      if (fileRequestTokens.current[role] !== requestToken) return;
      setDraft((current) => ({
        ...current,
        [role]: { kind: "csv", file, error: error instanceof Error ? error.message : "文件读取失败" },
      }));
    }
  }

  function setConnectorKind(role: "source" | "target", kind: ConnectorKind) {
    setDraft((current) => ({ ...current, [role]: { kind } }));
  }

  function toggleType(entityType: AgentEntityType, checked: boolean) {
    setDraft((current) => ({
      ...current,
      entityTypes: checked
        ? [...new Set([...current.entityTypes, entityType])]
        : current.entityTypes.filter((type) => type !== entityType),
    }));
  }

  async function createTask() {
    if (!draft.title.trim() || !draft.entityTypes.length || !readyConnector(draft.source) || !readyConnector(draft.target) || submissionState === "submitting") return;
    setSubmissionState("submitting");
    setSubmitError(undefined);
    try {
      const source = await uploadConnector(draft.source!, "authoritative");
      const target = await uploadConnector(draft.target!, "target");
      const task = await api.startManualTask({
        title: draft.title.trim(),
        entity_types: draft.entityTypes,
        source,
        target,
      }, sessionKey());
      setSubmissionState("created");
      navigate(`/tasks/${task.id}`);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "任务创建失败，请稍后重试");
      setSubmissionState("failed");
    }
  }

  async function uploadConnector(connector: ConnectorDraft, role: "authoritative" | "target") {
    if (connector.kind !== "csv") return { kind: connector.kind, configuration_id: connector.configurationId } satisfies AgentConnectorSelection;
    if (!connector.file) throw new Error("CSV 文件尚未选择");
    const upload = await ingestionApi.upload(connector.file, role);
    return { kind: "csv", upload_id: upload.id } satisfies AgentConnectorSelection;
  }

  const isSubmitting = submissionState === "submitting";
  const ready = Boolean(draft.title.trim() && draft.entityTypes.length && readyConnector(draft.source) && readyConnector(draft.target));

  return (
    <main className="page-shell external-sync-page">
      <header className="sync-page-heading">
        <span className="page-heading-mark sync-heading-mark"><RefreshCw size={20} /></span>
        <div>
          <h1>外部数据同步</h1>
          <p>配置数据连接后，由 Agent 负责全校数据接入、分析、治理和报告。</p>
        </div>
      </header>

      <section className="sync-methods" aria-labelledby="sync-method-title">
        <div className="section-title-row"><div><h2 id="sync-method-title">选择同步方式</h2><p>当前支持手动配置数据连接。</p></div></div>
        <div className="sync-method-entry">
          <button className={syncMethod === "manual" ? "sync-method active" : "sync-method"} type="button" aria-label="手动同步" aria-pressed={syncMethod === "manual"} disabled={isSubmitting} onClick={() => setSyncMethod("manual")}>
            <FileUp size={20} /><span><strong>手动同步</strong><small>配置三方系统与希沃魔方数据</small></span>
          </button>
        </div>
      </section>

      {syncMethod === "manual" && (
        <section className="manual-sync-form" aria-label="手动同步配置">
          <div className="sync-settings-grid">
            <label className="draft-field"><span>任务名称</span><input aria-label="同步任务名称" disabled={isSubmitting} value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} /></label>
            {(["source", "target"] as const).map((role) => {
              const connector = draft[role];
              const label = role === "source" ? "三方系统" : "希沃魔方";
              return (
                <fieldset className="draft-fieldset" key={role}>
                  <legend>{label}连接方式</legend>
                  <select aria-label={`${label}连接方式`} disabled={isSubmitting} value={connector?.kind ?? "csv"} onChange={(event) => setConnectorKind(role, event.target.value as ConnectorKind)}>
                    <option value="csv">CSV 文件</option><option value="api">API 连接</option><option value="database">数据库连接</option>
                  </select>
                  {connector?.kind === "csv" && <AttachmentPicker label={`${label} CSV`} inputLabel={`选择${label} CSV`} tone={role === "source" ? "source" : "target"} connector={connector} disabled={isSubmitting} onChange={(file) => void prepareFile(role, file)} />}
                  {connector?.kind !== "csv" && <input aria-label={`${label}配置 ID`} placeholder="输入后端配置 ID" disabled={isSubmitting} value={connector?.configurationId ?? ""} onChange={(event) => setDraft((current) => ({ ...current, [role]: { kind: connector?.kind ?? "api", configurationId: event.target.value } }))} />}
                </fieldset>
              );
            })}
            <fieldset className="draft-fieldset entity-checks"><legend>同步对象</legend><div className="draft-entity-grid">{entityTypes.map((entityType) => <Checkbox key={entityType} aria-label={entityLabels[entityType]} checked={draft.entityTypes.includes(entityType)} disabled={isSubmitting} onChange={(event) => toggleType(entityType, event.target.checked)}>{entityLabels[entityType]}</Checkbox>)}</div><button className="text-button" type="button" disabled={isSubmitting} onClick={() => setDraft((current) => ({ ...current, entityTypes: [] }))}>清空选择</button></fieldset>
          </div>
          {submitError && <Alert className="draft-error" type="error" showIcon message={submitError} />}
          <Button className="sync-start-button" type="primary" size="large" loading={isSubmitting} disabled={!ready || isSubmitting} onClick={() => void createTask()}>开始同步</Button>
          <p className="draft-footnote">提交后进入 Agent 持久化进度，不会由浏览器直接修改数据。</p>
        </section>
      )}
    </main>
  );
}

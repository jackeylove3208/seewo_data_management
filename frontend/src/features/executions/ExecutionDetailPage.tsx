import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, FileText, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { apiUrl } from "../../api/client";
import { reportingApi, type RestorePreview } from "../../api/reporting";
import { BackButton } from "../../components/BackButton";

function message(error: unknown) {
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}

function Facts({ value }: { value: Record<string, unknown> | null }) {
  return <pre className="fact-block">{value ? JSON.stringify(value, null, 2) : "-"}</pre>;
}

export function ExecutionDetailPage() {
  const { executionId = "" } = useParams();
  const queryClient = useQueryClient();
  const [preview, setPreview] = useState<RestorePreview | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [restoreKey, setRestoreKey] = useState("");
  const [completedBatchId, setCompletedBatchId] = useState<string | null>(null);

  const execution = useQuery({
    queryKey: ["execution", executionId],
    queryFn: () => reportingApi.execution(executionId),
  });
  const reports = useQuery({
    queryKey: ["reports", executionId],
    queryFn: () => reportingApi.reports(executionId),
    enabled: Boolean(execution.data),
  });
  const versions = useQuery({
    queryKey: ["versions", execution.data?.task_id],
    queryFn: () => reportingApi.versions(execution.data!.task_id),
    enabled: Boolean(execution.data?.task_id),
  });
  const generate = useMutation({
    mutationFn: () => reportingApi.generateReport(executionId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reports", executionId] }),
  });
  const restorePreview = useMutation({
    mutationFn: reportingApi.previewRestore,
    onSuccess: (value) => {
      setPreview(value);
      setAcknowledged(false);
      setRestoreKey(crypto.randomUUID());
      setCompletedBatchId(null);
    },
  });
  const restore = useMutation({
    mutationFn: async () => {
      const confirmation = await reportingApi.confirmRestore(preview!.preview_hash, restoreKey);
      setCompletedBatchId(confirmation.batch_id);
      await reportingApi.executeRestore(confirmation.restore_request_id);
      return confirmation;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["versions", execution.data?.task_id] });
    },
  });

  if (execution.isLoading) {
    return <main className="operations-page"><p>正在加载执行详情...</p></main>;
  }
  if (!execution.data) {
    return <main className="operations-page"><p role="alert">执行详情不可用</p></main>;
  }

  const canReport = execution.data.permitted_actions.includes("report");
  const canRestore = execution.data.permitted_actions.includes("restore");
  const currentVersionId = versions.data?.at(-1)?.id;

  return (
    <main className="operations-page">
      <BackButton fallback="/executions" label="返回执行历史" />
      <header className="operations-header">
        <div><h1>执行与恢复</h1><p>{execution.data.id}</p></div>
        <span className="status-label">{execution.data.status}</span>
      </header>

      <section className="operations-band execution-metadata" aria-label="执行信息">
        <dl>
          <div><dt>确认人</dt><dd>{execution.data.confirmed_by}</dd></div>
          <div><dt>确认时间</dt><dd>{new Date(execution.data.confirmed_at).toLocaleString()}</dd></div>
          <div><dt>输入版本</dt><dd>{execution.data.input_target_version_id}</dd></div>
          <div><dt>计划版本</dt><dd>v{execution.data.plan_version}</dd></div>
        </dl>
      </section>

      <section className="operations-band">
        <div className="section-heading">
          <div><h2>治理报告</h2><p>基于固定执行事实生成的不可变版本</p></div>
          <button
            className="command-button"
            onClick={() => generate.mutate()}
            disabled={!canReport || generate.isPending}
          >
            <FileText size={16} />{generate.isPending ? "正在生成..." : "生成治理报告"}
          </button>
        </div>
        {generate.isError && <p role="alert">{message(generate.error)}</p>}
        <div className="report-list">
          {reports.data?.map((report) => (
            <div className="report-row" key={report.id}>
              <span><strong>报告 v{report.version}</strong><small>{report.content.summary}</small></span>
              <a href={apiUrl(reportingApi.reportHtmlUrl(report.id))} target="_blank" rel="noreferrer" title="查看 HTML 报告"><ExternalLink size={16} /></a>
              <a href={apiUrl(reportingApi.reportDownloadUrl(report.id))} title="下载 HTML 报告"><Download size={16} /></a>
            </div>
          ))}
          {reports.data?.length === 0 && <p>尚未生成报告</p>}
        </div>
      </section>

      <section className="operations-band">
        <div className="section-heading"><div><h2>操作事实</h2><p>不可变 before/after 与最终状态</p></div></div>
        <div className="operation-table-wrap">
          <table className="operation-table">
            <thead><tr><th>类型</th><th>实体</th><th>Before</th><th>After</th><th>结果</th></tr></thead>
            <tbody>{execution.data.operations.map((operation) => (
              <tr key={operation.record_id}>
                <td>{operation.operation_type}</td>
                <td>{operation.entity_type}<small>{operation.target_source_identifier ?? "新建实体"}</small></td>
                <td><Facts value={operation.before} /></td>
                <td><Facts value={operation.after} /></td>
                <td>{operation.attempts.at(-1)?.status ?? "pending"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>

      <section className="operations-band">
        <div className="section-heading"><div><h2>审计时间线</h2><p>后端身份与执行事件</p></div></div>
        <ol className="audit-timeline">
          {execution.data.audit_events.map((event) => (
            <li key={event.id}><strong>{event.event_type}</strong><span>{event.actor_id} · {new Date(event.created_at).toLocaleString()}</span></li>
          ))}
          {execution.data.audit_events.length === 0 && <li>暂无审计事件</li>}
        </ol>
      </section>

      <section className="operations-band">
        <div className="section-heading"><div><h2>历史恢复点</h2><p>恢复会创建新的执行和目标版本，不删除现有历史</p></div></div>
        {versions.isError && <p role="alert">{message(versions.error)}</p>}
        <div className="version-timeline">
          {versions.data?.map((version) => (
            <div className="version-row" key={version.id}>
              <span><strong>{version.id.slice(0, 12)}</strong><small>{new Date(version.created_at).toLocaleString()}</small></span>
              {version.id === currentVersionId ? <em>当前</em> : (
                <button
                  aria-label={`恢复到 ${version.id}`}
                  title="预览恢复影响"
                  disabled={!canRestore || restorePreview.isPending}
                  onClick={() => restorePreview.mutate(version.id)}
                ><RotateCcw size={15} /></button>
              )}
            </div>
          ))}
        </div>
        {restorePreview.isError && <p role="alert">{message(restorePreview.error)}</p>}
        {preview && (
          <div className="restore-review">
            <strong>{preview.allowed ? `将执行 ${preview.operations.length} 项恢复操作` : "当前恢复被阻止"}</strong>
            {preview.explanation_state === "available" && preview.explanation && <p>{preview.explanation}</p>}
            {preview.explanation_state !== "available" && <p>AI 影响说明不可用，以下确定性预检结果仍然有效。</p>}
            {preview.conflicts.map((item, index) => (
              <p role="alert" key={`${item.code}-${item.operation_id ?? index}`}>{item.message}{item.operation_id ? `（操作 ${item.operation_id}）` : ""}</p>
            ))}
            {preview.operations.map((operation) => (
              <div className="restore-operation" key={operation.id}>
                <strong>{operation.operation_type} · {operation.entity_type} · {operation.risk}</strong>
                <div><Facts value={operation.before} /><span>→</span><Facts value={operation.after} /></div>
              </div>
            ))}
            {preview.allowed && (
              <>
                <label><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />我已确认恢复会创建高风险补偿批次</label>
                <button className="command-button danger" disabled={!acknowledged || restore.isPending} onClick={() => restore.mutate()}>{restore.isPending ? "正在执行恢复..." : "确认并执行恢复"}</button>
              </>
            )}
            {restore.isError && <p role="alert">{message(restore.error)}。补偿批次已保留，可进入执行详情查看状态或重试。</p>}
            {completedBatchId && <p className="success-message">恢复执行已创建：<Link to={`/executions/${completedBatchId}`}>查看执行 {completedBatchId}</Link></p>}
          </div>
        )}
      </section>
    </main>
  );
}

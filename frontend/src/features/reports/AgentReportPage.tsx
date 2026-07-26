import { useQuery } from "@tanstack/react-query";
import { Alert, Empty, Skeleton, Tag } from "antd";
import { CheckCircle2, FileCheck2, ShieldAlert, Sparkles } from "lucide-react";
import { useParams } from "react-router-dom";

import { agentApi } from "../../api/agent";
import { BackButton } from "../../components/BackButton";

type ReportItem = Record<string, unknown>;

function record(value: unknown): ReportItem {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
    ? value as ReportItem
    : {};
}

function records(value: unknown): ReportItem[] {
  return Array.isArray(value)
    ? value.filter((item): item is ReportItem => Boolean(item) && typeof item === "object")
    : [];
}

function text(value: unknown, fallback = "—") {
  return typeof value === "string" || typeof value === "number" ? String(value) : fallback;
}

const terminalLabels: Record<string, string> = {
  completed: "同步完成",
  terminated: "任务已终止",
  failed: "处理失败",
  abnormal_input: "输入异常",
};

const operationLabels: Record<string, string> = {
  create: "新增",
  update: "修改",
  delete: "删除",
  retain: "保留",
  skip: "跳过",
};

const entityLabels: Record<string, string> = {
  department: "部门",
  student: "学生",
  teacher: "教师",
};

const decisionLabels: Record<string, string> = {
  approved: "已同意",
  rejected: "已拒绝",
  succeeded: "执行成功",
  failed: "执行失败",
  blocked: "未执行",
  not_required: "无需审批",
  not_executed: "未执行",
  verification_failed: "校验失败",
};

export function AgentReportPage() {
  const { taskId = "" } = useParams();
  const report = useQuery({
    queryKey: ["agent-report", taskId],
    queryFn: ({ signal }) => agentApi.report(taskId, signal),
  });

  if (report.isLoading) {
    return <main className="page-shell apple-page agent-report-page"><BackButton fallback={`/tasks/${taskId}`} label="返回任务详情" /><Skeleton active paragraph={{ rows: 8 }} /></main>;
  }
  if (report.isError || !report.data) {
    return <main className="page-shell apple-page agent-report-page"><BackButton fallback={`/tasks/${taskId}`} label="返回任务详情" /><Alert type="error" showIcon message="任务报告读取失败" description="报告可能尚未生成，请稍后重试。" /></main>;
  }

  const facts = report.data.facts;
  const narrative = record(report.data.content.narrative);
  const findings = records(facts.findings);
  const excluded = records(facts.excluded_findings ?? facts.invalid_rows);
  const mutations = records(facts.mutations);
  const mutationSummary = record(facts.mutation_summary);
  const publication = record(facts.publication);
  const reportTitle = text(
    narrative.title_zh,
    report.data.kind === "rollback" ? "回滚任务分析报告" : "数据同步分析报告",
  );
  const reportSummary = text(
    narrative.summary_zh ?? narrative.summary,
    findings.length
      ? `Agent 共发现 ${findings.length} 项需要处理的问题，并依据审核结果完成治理。`
      : "Agent 已完成本次数据核验，没有发现需要治理的问题。",
  );
  const publicationStatus = text(publication.status, "not_applicable");

  return (
    <main className="page-shell apple-page agent-report-page">
      <BackButton fallback={`/tasks/${taskId}`} label="返回任务详情" />
      <section className="agent-report-hero">
        <div>
          <p className="eyebrow"><Sparkles size={14} /> AGENT REPORT</p>
          <h1>{reportTitle}</h1>
          <p className="agent-report-lead">{reportSummary}</p>
        </div>
        <Tag color={report.data.terminal_state === "completed" ? "success" : "warning"}>
          {terminalLabels[report.data.terminal_state] ?? report.data.terminal_state}
        </Tag>
      </section>

      <section className="agent-report-metrics" aria-label="报告摘要">
        <article><span>需要处理</span><strong>{findings.length}</strong></article>
        <article><span>输入异常</span><strong>{excluded.length}</strong></article>
        <article><span>成功变更</span><strong>{text(mutationSummary.succeeded, "0")}</strong></article>
        <article><span>失败变更</span><strong>{text(mutationSummary.failed, "0")}</strong></article>
      </section>

      {publicationStatus !== "not_applicable" && (
        <section className="agent-report-publication">
          <span className="agent-report-section-icon"><FileCheck2 size={20} /></span>
          <div>
            <h2>
              {publicationStatus === "published"
                ? "已写回本地 CSV"
                : publicationStatus === "no_changes"
                  ? "本地 CSV 无需修改"
                  : "本地 CSV 已核验"}
            </h2>
            <p>{text(publication.source_ref, "已授权的希沃目标文件")}</p>
          </div>
        </section>
      )}

      <section className="agent-report-section">
        <header>
          <span className="agent-report-section-icon"><ShieldAlert size={20} /></span>
          <div><p>ANALYSIS</p><h2>问题分析与治理方案</h2></div>
        </header>
        {findings.length ? (
          <ol className="agent-report-findings">
            {findings.map((item, index) => {
              const identity = [
                text(item.entity_name, ""),
                item.entity_number ? `编号 ${text(item.entity_number)}` : "",
                text(item.class_name, ""),
              ].filter(Boolean).join(" · ");
              const operatorDecision = text(item.operator_decision, "");
              const state = operatorDecision && operatorDecision !== "not_required"
                ? operatorDecision
                : text(item.execution_status, operatorDecision);
              return (
                <li key={text(item.id, `finding-${index}`)}>
                  <div className="agent-report-finding-heading">
                    <div>
                      <span>问题 {index + 1}</span>
                      <h3>{text(item.category_zh ?? item.kind, "未分类问题")}</h3>
                    </div>
                    {state && <Tag color={state === "rejected" || state === "failed" ? "error" : "success"}>{decisionLabels[state] ?? state}</Tag>}
                  </div>
                  {identity && <p className="agent-report-identity">{identity}</p>}
                  <p>{text(item.analysis_zh ?? item.reason, "Agent 已记录结构化问题证据。")}</p>
                  {typeof item.solution_zh === "string" && item.solution_zh && (
                    <div className="agent-report-solution">
                      <strong>AI 治理方案</strong>
                      <p>{text(item.solution_zh)}</p>
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        ) : <Empty description="没有需要治理的问题" />}
      </section>

      {excluded.length > 0 && (
        <section className="agent-report-section">
          <header>
            <span className="agent-report-section-icon"><ShieldAlert size={20} /></span>
            <div><p>EXCEPTIONS</p><h2>输入异常与排除项</h2></div>
          </header>
          <ul className="agent-report-exclusions">
            {excluded.map((item, index) => (
              <li key={`${text(item.source, "input")}-${index}`}>
                {text(item.reason ?? item.disposition ?? item.source, "输入数据不符合规范")}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="agent-report-section">
        <header>
          <span className="agent-report-section-icon"><CheckCircle2 size={20} /></span>
          <div><p>EXECUTION</p><h2>治理执行结果</h2></div>
        </header>
        {mutations.length ? (
          <ul className="agent-report-mutations">
            {mutations.map((item, index) => (
              <li key={text(item.id, `mutation-${index}`)}>
                <Tag color={item.status === "succeeded" ? "success" : "error"}>
                  {decisionLabels[text(item.status)] ?? text(item.status)}
                </Tag>
                <span>
                  {operationLabels[text(item.operation)] ?? text(item.operation, "操作")}
                  {" · "}
                  {entityLabels[text(item.entity_kind)] ?? text(item.entity_kind, "实体")}
                </span>
              </li>
            ))}
          </ul>
        ) : <Empty description="本任务没有修改目标数据" />}
      </section>
    </main>
  );
}

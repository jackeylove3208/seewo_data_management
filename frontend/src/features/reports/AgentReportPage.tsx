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

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string | number => typeof item === "string" || typeof item === "number")
      .map(String)
    : [];
}

function uniqueRecordsById(items: ReportItem[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    const id = text(item.id, "");
    if (!id || seen.has(id)) {
      return !id;
    }
    seen.add(id);
    return true;
  });
}

function text(value: unknown, fallback = "—") {
  return typeof value === "string" || typeof value === "number" ? String(value) : fallback;
}

function count(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
}

function localizedLabels(
  values: string[],
  labels: Record<string, string>,
  fallback: string,
) {
  return [...new Set(values.map((value) => labels[value] ?? value))]
    .sort()
    .join("、") || fallback;
}

function includedQualityWarningAnalyses(facts: ReportItem) {
  const includedFindings = records(facts.excluded_findings ?? facts.invalid_rows).filter(
    (item) => text(item.reason, "") === "authority_field_unavailable"
      && text(item.inclusion_state, "") === "included",
  );
  if (!includedFindings.length) {
    return [];
  }

  const entityKinds = includedFindings.map(
    (item) => text(record(item.safe_evidence).entity_kind, ""),
  ).filter(Boolean);
  const affectedFields = includedFindings.flatMap(
    (item) => strings(item.affected_fields),
  );
  const inputDiagnostics = record(facts.input_diagnostics);
  const reasonCounts = record(inputDiagnostics.reason_counts);
  const reasonCount = reasonCounts.authority_field_unavailable;
  const includedCount = typeof reasonCount === "number" && reasonCount >= 0
    ? reasonCount
    : includedFindings.length;
  const entityZh = localizedLabels(entityKinds, { student: "学生" }, "记录");
  const fieldZh = localizedLabels(
    affectedFields,
    { class_name: "班级信息", email: "邮箱" },
    "字段信息",
  );

  return [{
    reason_code: "authority_field_unavailable",
    title_zh: `权威${entityZh}数据缺少${fieldZh}`,
    analysis_zh: `权威${entityZh}数据中有 ${includedCount} 条记录缺少${fieldZh}。`,
    impact_zh: `${fieldZh}不可用仅作为数据质量提醒；这些${entityZh}仍保留在匹配与同步范围内，允许同步。`,
    suggestion_zh: `建议补充${fieldZh}以提升数据质量；已完成的同步无需重试。`,
  }];
}

const terminalLabels: Record<string, string> = {
  completed: "同步完成",
  completed_with_conflicts: "回滚存在冲突",
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
  already_restored: "已经恢复，无需再次写入",
  conflict_skipped: "因当前值冲突而跳过",
};

function reportStatusColor(status: string) {
  if (status === "approved" || status === "succeeded" || status === "already_restored") {
    return "success";
  }
  if (status === "blocked" || status === "conflict_skipped" || status === "not_executed") {
    return "warning";
  }
  return "error";
}

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
  const narrativeExceptionAnalyses = records(narrative.input_exception_analyses);
  const includedQualityWarnings = includedQualityWarningAnalyses(facts);
  const includedQualityWarningsByReason = new Map(
    includedQualityWarnings.map((item) => [text(item.reason_code, ""), item]),
  );
  const exceptionAnalyses = [
    ...narrativeExceptionAnalyses.map(
      (item) => includedQualityWarningsByReason.get(text(item.reason_code, "")) ?? item,
    ),
    ...includedQualityWarnings.filter(
      (item) => !narrativeExceptionAnalyses.some(
        (narrativeItem) => text(narrativeItem.reason_code, "") === text(item.reason_code, ""),
      ),
    ),
  ];
  const findings = uniqueRecordsById(records(facts.findings));
  const governanceFindings = findings.filter(
    (item) => text(item.kind, "") !== "authority_invalid",
  );
  const excluded = records(facts.excluded_findings ?? facts.invalid_rows);
  const inputDiagnostics = record(facts.input_diagnostics);
  const overlappedReasonCounts = record(inputDiagnostics.overlapped_reason_counts);
  const exceptionReasonCodes = new Set(
    exceptionAnalyses.map((item) => text(item.reason_code, "")).filter(Boolean),
  );
  const overlappedReasonCodes = new Set(Object.keys(overlappedReasonCounts));
  const remainingExclusions = excluded.filter(
    (item) => {
      const reason = text(item.reason, "");
      const inclusionState = text(item.inclusion_state, "");
      if (
        reason === "authority_field_unavailable"
        && includedQualityWarningsByReason.has(reason)
        && (inclusionState === "excluded" || inclusionState === "anomaly")
      ) {
        return true;
      }
      return !exceptionReasonCodes.has(reason) && !overlappedReasonCodes.has(reason);
    },
  );
  const inputExceptionCount = count(
    inputDiagnostics.unique_marked_input_count ?? excluded.length,
  );
  const mutations = records(facts.mutations);
  const mutationSummary = record(facts.mutation_summary);
  const publication = record(facts.publication);
  const terminationContext = record(facts.termination_context);
  const isRollback = report.data.kind === "rollback";
  const isTerminated = report.data.terminal_state === "terminated";
  const isAbnormalInput = report.data.terminal_state === "abnormal_input";
  const isFailed = report.data.terminal_state === "failed";
  const reportTitle = text(
    narrative.title_zh,
    report.data.kind === "rollback" ? "回滚任务分析报告" : "数据同步分析报告",
  );
  const reportSummary = text(
    narrative.summary_zh ?? narrative.summary,
    isRollback
      ? report.data.terminal_state === "completed_with_conflicts"
        ? "部分数据的当前值与可安全回滚的值不一致，系统已跳过这些冲突项。"
        : "目标数据已经完成回滚；已处于原状态的记录不会重复写入。"
      : governanceFindings.length
      ? `Agent 共发现 ${governanceFindings.length} 项需要处理的问题，并依据审核结果完成治理。`
      : "Agent 已完成本次数据核验，没有发现需要治理的问题。",
  );
  const publicationStatus = text(publication.status, "not_applicable");
  const failedMutationCount = count(mutationSummary.failed)
    + count(mutationSummary.verification_failed);
  const hasFailedMutations = failedMutationCount > 0;
  const analysisEmptyDescription = isTerminated
    ? "任务在完成问题分析前已终止"
    : isAbnormalInput
      ? "输入异常阻止了治理分析"
      : isFailed
        ? "任务失败前未形成可执行治理问题"
        : "没有需要治理的问题";
  const executionEmptyDescription = isTerminated
    ? "任务终止前没有修改目标数据"
    : isAbnormalInput
      ? "输入异常阻止了治理执行"
      : isFailed
        ? "任务失败前没有完成目标数据修改"
        : "本任务没有修改目标数据";

  return (
    <main className="page-shell apple-page agent-report-page">
      <BackButton fallback={`/tasks/${taskId}`} label="返回任务详情" />
      <section className="agent-report-hero">
        <div>
          <p className="eyebrow"><Sparkles size={14} /> AGENT REPORT</p>
          <h1>{reportTitle}</h1>
          <p className="agent-report-lead">{reportSummary}</p>
        </div>
        <Tag
          color={hasFailedMutations
            ? "error"
            : report.data.terminal_state === "completed"
              ? "success"
              : "warning"}
        >
          {hasFailedMutations
            ? "部分完成"
            : isRollback && report.data.terminal_state === "completed"
            ? "回滚完成"
            : terminalLabels[report.data.terminal_state] ?? report.data.terminal_state}
        </Tag>
      </section>

      <section className="agent-report-metrics" aria-label="报告摘要">
        {isRollback ? (
          <>
            <article><span>实际恢复</span><strong>{text(mutationSummary.succeeded, "0")}</strong></article>
            <article><span>已处于原状态</span><strong>{text(mutationSummary.already_restored, "0")}</strong></article>
            <article><span>冲突跳过</span><strong>{text(mutationSummary.conflict_skipped, "0")}</strong></article>
            <article className={hasFailedMutations ? "agent-report-metric-error" : undefined}>
              <span>恢复失败</span><strong>{failedMutationCount}</strong>
            </article>
          </>
        ) : (
          <>
            <article><span>需要处理</span><strong>{governanceFindings.length}</strong></article>
            <article><span>输入异常</span><strong>{inputExceptionCount}</strong></article>
            <article><span>成功变更</span><strong>{text(mutationSummary.succeeded, "0")}</strong></article>
            <article className={hasFailedMutations ? "agent-report-metric-error" : undefined}>
              <span>失败变更</span><strong>{failedMutationCount}</strong>
            </article>
          </>
        )}
      </section>

      {isTerminated && (
        <section className="agent-report-section">
          <header>
            <span className="agent-report-section-icon"><ShieldAlert size={20} /></span>
            <div><p>TERMINATION</p><h2>任务终止说明</h2></div>
          </header>
          <dl className="start-confirmation-details">
            <div>
              <dt>终止原因</dt>
              <dd>{text(terminationContext.reason_zh, "任务按操作人要求终止")}</dd>
            </div>
            <div>
              <dt>终止阶段</dt>
              <dd>{text(terminationContext.phase_zh, "报告生成")}</dd>
            </div>
            <div>
              <dt>已记录问题</dt>
              <dd>{count(terminationContext.recorded_finding_count)}</dd>
            </div>
            <div>
              <dt>已验证修改</dt>
              <dd>{count(terminationContext.verified_mutation_count)}</dd>
            </div>
          </dl>
        </section>
      )}

      {publicationStatus !== "not_applicable" && (
        <section className="agent-report-publication">
          <span className="agent-report-section-icon"><FileCheck2 size={20} /></span>
          <div>
            <h2>
              {publicationStatus === "published"
                ? isRollback ? "已写回回滚结果" : "已写回本地 CSV"
                : publicationStatus === "no_changes"
                  ? isRollback ? "无需再次写入本地 CSV" : "本地 CSV 无需修改"
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
        {governanceFindings.length ? (
          <ol className="agent-report-findings">
            {governanceFindings.map((item, index) => {
              const identity = [
                text(item.entity_name, ""),
                item.entity_number ? `编号 ${text(item.entity_number)}` : "",
                text(item.class_name, ""),
              ].filter(Boolean).join(" · ");
              const operatorDecision = text(item.operator_decision, "");
              const executionStatus = text(item.execution_status, "");
              const states = [...new Set(
                [operatorDecision, executionStatus].filter(
                  (state) => state && state !== "not_required",
                ),
              )];
              return (
                <li key={text(item.id, `finding-${index}`)}>
                  <div className="agent-report-finding-heading">
                    <div>
                      <span>问题 {index + 1}</span>
                      <h3>{text(item.category_zh ?? item.kind, "未分类问题")}</h3>
                    </div>
                    {states.length > 0 && (
                      <div className="agent-report-status-tags">
                        {states.map((state) => (
                          <Tag key={state} color={reportStatusColor(state)}>
                            {decisionLabels[state] ?? state}
                          </Tag>
                        ))}
                      </div>
                    )}
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
        ) : <Empty description={analysisEmptyDescription} />}
      </section>

      {(exceptionAnalyses.length > 0 || remainingExclusions.length > 0) && (
        <section className="agent-report-section">
          <header>
            <span className="agent-report-section-icon"><ShieldAlert size={20} /></span>
            <div>
              <p>EXCEPTIONS</p>
              <h2>{includedQualityWarnings.length ? "数据质量提醒与排除项" : "输入异常与排除项"}</h2>
            </div>
          </header>
          {exceptionAnalyses.length > 0 && (
            <ol className="agent-report-exception-analyses">
              {exceptionAnalyses.map((item, index) => (
                <li
                  className="agent-report-exception-analysis"
                  key={text(item.reason_code, `exception-analysis-${index}`)}
                >
                  <h3>
                    {text(item.title_zh, "输入异常分析")}
                    {includedQualityWarningsByReason.has(text(item.reason_code, "")) && (
                      <Tag color="blue">允许同步</Tag>
                    )}
                  </h3>
                  <p>{text(item.analysis_zh)}</p>
                  <p><strong>影响：</strong>{text(item.impact_zh)}</p>
                  <p><strong>建议：</strong>{text(item.suggestion_zh)}</p>
                </li>
              ))}
            </ol>
          )}
          {remainingExclusions.length > 0 && (
            <ul className="agent-report-exclusions">
              {remainingExclusions.map((item, index) => (
                <li key={`${text(item.source, "input")}-${index}`}>
                  <span>
                    {text(item.reason ?? item.disposition ?? item.source, "输入数据不符合规范")}
                  </span>
                  <Tag color="orange">输入异常</Tag>
                </li>
              ))}
            </ul>
          )}
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
                <Tag color={reportStatusColor(text(item.status))}>
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
        ) : <Empty description={executionEmptyDescription} />}
      </section>
    </main>
  );
}

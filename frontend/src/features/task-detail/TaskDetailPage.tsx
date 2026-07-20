import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Empty, Progress, Skeleton, Tag } from "antd";
import { ArrowRight, Check, CircleDot, FileInput, RotateCcw, ScanSearch, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { queryKeys } from "../../api/queryKeys";
import { reconciliationApi, type WorkflowStage } from "../../api/reconciliation";
import { BackButton } from "../../components/BackButton";
import { demoEntitySummaries, differencesFor, entityLabels } from "../../data/demoDifferences";
import { findTask } from "../../data/taskHistory";
import type { EntitySummary } from "../../types/domain";
import { BatchAnalysisModal } from "../analysis/BatchAnalysisModal";
import { getSelectionState, issueIdsFor, toggleCategory } from "../differences/selection";
import { useIssueSelection } from "../differences/useIssueSelection";
import { useAnalysisJob } from "../workflow/useAnalysisJob";
import { useReconciliationWorkflow } from "../workflow/useReconciliationWorkflow";

const stages = [
  { id: "ingestion", label: "数据接入", icon: FileInput },
  { id: "matching", label: "实体解析", icon: ScanSearch },
  { id: "differences", label: "差异检测", icon: CircleDot },
  { id: "analysis", label: "AI 分析", icon: Sparkles },
] as const;

const stageIndex: Record<WorkflowStage, number> = {
  ingestion: 0,
  matching: 1,
  differences: 2,
  analysis: 3,
  complete: 4,
};

function updatedAtLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function TaskDetailPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const historyTask = findTask(taskId);
  const demo = Boolean(historyTask?.isDemo);
  const workflow = useReconciliationWorkflow(taskId, !demo);
  const [batchOpen, setBatchOpen] = useState(false);
  const { selection, setSelection } = useIssueSelection(taskId);
  const task = workflow.task.data;
  const currentWorkflow = task?.workflow;
  const analysisJobId = currentWorkflow?.analysis.job_id;
  const analysisJob = useAnalysisJob(analysisJobId, !demo && Boolean(analysisJobId));
  const liveAnalysis = analysisJob.job.data;
  const analysisInProgress = Boolean(liveAnalysis && ["queued", "running"].includes(liveAnalysis.status));
  const terminalJob = Boolean(liveAnalysis && ["completed", "completed_with_failures"].includes(liveAnalysis.status));
  const analysisCanceled = liveAnalysis?.status === "canceled";
  const analysisTerminal = demo || (analysisJobId ? terminalJob : currentWorkflow?.stage === "complete");
  const summaryQuery = useQuery({
    queryKey: queryKeys.analysisSummary(taskId),
    queryFn: ({ signal }) => reconciliationApi.getAnalysisSummary(taskId, signal),
    enabled: !demo && analysisTerminal,
  });

  if (!demo && !task && workflow.task.isLoading) {
    return <main className="page-shell task-detail-page"><BackButton fallback="/tasks" label="返回任务列表" /><Skeleton active paragraph={{ rows: 8 }} /></main>;
  }
  if ((!historyTask && workflow.task.isError) || (demo && !historyTask)) {
    return (
      <main className="page-shell empty-page">
        <BackButton fallback="/tasks" label="返回任务列表" />
        <h1>没有找到这个任务</h1>
        <p>任务可能已被清理，或当前账号没有访问权限。</p>
      </main>
    );
  }

  const realSummaries = summaryQuery.data?.entity_types.filter((item) => item.issue_count > 0) ?? [];
  const summaries: EntitySummary[] = demo
    ? demoEntitySummaries
    : realSummaries.map((item) => ({
      type: item.entity_type,
      label: entityLabels[item.entity_type],
      sourceCount: historyTask?.entityCounts?.[item.entity_type]?.source ?? 0,
      targetCount: historyTask?.entityCounts?.[item.entity_type]?.target ?? 0,
      issueCount: item.issue_count,
    }));
  const totalIssues = demo ? historyTask!.issueCount : realSummaries.reduce((total, item) => total + item.issue_count, 0);
  const proposalReady = realSummaries.reduce((total, item) => total + item.proposal_ready, 0);
  const summaryReady = demo || Boolean(analysisTerminal && summaryQuery.data?.terminal);
  const taskTitle = historyTask?.title ?? `对账任务 ${taskId.slice(0, 8)}`;
  const sourceFile = historyTask?.sourceFile ?? "三方系统快照";
  const targetFile = historyTask?.targetFile ?? "希沃快照";
  const currentIndex = currentWorkflow ? stageIndex[currentWorkflow.stage] : 4;
  const effectiveIndex = analysisTerminal ? stageIndex.complete : analysisJobId ? stageIndex.analysis : currentIndex;

  return (
    <main className="page-shell task-detail-page">
      <BackButton fallback="/tasks" label="返回任务列表" />
      <section className="detail-heading">
        <div>
          <span className="heading-tags">
            <Tag color={currentWorkflow?.status === "failed" ? "error" : analysisCanceled ? "warning" : analysisTerminal ? "success" : "processing"}>
              {currentWorkflow?.status === "failed" ? "处理失败" : analysisCanceled ? "分析已取消" : analysisTerminal ? "分析完成" : "处理中"}
            </Tag>
            {demo && <Tag>演示差异</Tag>}
          </span>
          <h1>{taskTitle}</h1>
          <p>{sourceFile} <ArrowRight size={13} /> {targetFile}</p>
        </div>
        <div className="detail-total"><span>发现问题</span><strong>{summaryReady ? totalIssues : "--"}</strong></div>
      </section>

      {workflow.task.isError && <Alert className="workflow-alert" type="error" showIcon message="任务状态读取失败" description="请求未完成，请稍后重试。" action={<Button size="small" onClick={() => void workflow.task.refetch()}>重试</Button>} />}
      {workflow.advanceError && <Alert className="workflow-alert" type="error" showIcon message="任务处理未继续" description="自动处理请求未完成，请确认后继续。" action={<Button size="small" loading={workflow.advancing} onClick={workflow.continueAdvance}>继续处理</Button>} />}
      {analysisJob.job.isError && <Alert className="workflow-alert" type="error" showIcon message="AI 分析进度读取失败" description="正在通过轮询重新连接，请稍后重试。" action={<Button size="small" onClick={() => void analysisJob.job.refetch()}>重试</Button>} />}
      {analysisTerminal && summaryQuery.isError && <Alert className="workflow-alert" type="error" showIcon message="问题汇总读取失败" description="暂时无法读取分析结果，请稍后重试。" action={<Button size="small" onClick={() => void summaryQuery.refetch()}>重试问题汇总</Button>} />}
      {(workflow.retryError || analysisJob.retryError || analysisJob.cancelError) && <Alert className="workflow-alert" type="error" showIcon message="操作未完成" description="请求未完成，请稍后重试。" />}
      {currentWorkflow?.status === "failed" && (
        <Alert className="workflow-alert" type="error" showIcon message="任务处理失败" description="当前阶段未完成，请重试或联系管理员。" action={workflow.canRetry ? <Button icon={<RotateCcw size={14} />} loading={workflow.retrying} onClick={workflow.retry}>重试当前阶段</Button> : undefined} />
      )}

      <section className="stage-track" aria-label="任务处理阶段">
        {stages.map((stage, index) => {
          const isAnalysis = stage.id === "analysis";
          const completed = demo || effectiveIndex > index;
          const active = !demo && !analysisTerminal && !analysisCanceled && (
            (isAnalysis && analysisInProgress)
            || (!analysisJobId && currentIndex === index && currentWorkflow?.status !== "failed")
          );
          const failed = !demo && currentIndex === index && currentWorkflow?.status === "failed";
          const Icon = stage.icon;
          let statusText = completed ? "已完成" : "等待处理";
          if (active) statusText = isAnalysis ? "AI 分析中" : "正在处理";
          if (isAnalysis && analysisCanceled) statusText = "分析已取消";
          if (failed) statusText = "处理失败";
          return (
            <div className={`stage${completed ? " completed" : ""}${active ? " active" : ""}${failed ? " failed" : ""}`} key={stage.id}>
              <span className="stage-icon">{completed ? <Check size={15} /> : <Icon className={active && isAnalysis ? "spin" : ""} size={15} />}</span>
              <span className="stage-copy"><strong>{stage.label}</strong><small>{statusText}</small></span>
              {active && isAnalysis && currentWorkflow && (
                <div className="stage-analysis-progress">
                  <span>已完成 {liveAnalysis?.completed ?? currentWorkflow.analysis.completed} / {liveAnalysis?.total ?? currentWorkflow.analysis.total}</span>
                  <Progress percent={(liveAnalysis?.total ?? currentWorkflow.analysis.total) ? Math.round((liveAnalysis?.completed ?? currentWorkflow.analysis.completed) / (liveAnalysis?.total ?? currentWorkflow.analysis.total) * 100) : 0} showInfo={false} size="small" />
                  <small>可生成方案 {liveAnalysis?.proposal_ready ?? 0} · 待补信息 {liveAnalysis?.needs_information ?? 0} · 仅人工 {liveAnalysis?.manual_only ?? currentWorkflow.analysis.manual_review} · 失败 {liveAnalysis?.failed ?? currentWorkflow.analysis.failed}</small>
                  {liveAnalysis?.updated_at && <small>最近更新 {updatedAtLabel(liveAnalysis.updated_at)}</small>}
                  {liveAnalysis && ["queued", "running"].includes(liveAnalysis.status) && <Button size="small" icon={<X size={13} />} loading={analysisJob.canceling} onClick={analysisJob.cancel}>取消分析</Button>}
                </div>
              )}
            </div>
          );
        })}
      </section>

      {analysisCanceled && <Alert className="workflow-alert" type="warning" showIcon message="分析已取消" description="已完成的结果会保留，继续后只处理尚未完成的差异。" action={<Button icon={<RotateCcw size={14} />} loading={analysisJob.retrying} onClick={analysisJob.retry}>继续分析</Button>} />}
      {liveAnalysis?.status === "completed_with_failures" && liveAnalysis.failed > 0 && <Alert className="workflow-alert" type="warning" showIcon message={`有 ${liveAnalysis.failed} 项分析失败`} description="已成功和人工处理的结果会保留，只重试失败项。" action={<Button icon={<RotateCcw size={14} />} loading={analysisJob.retrying} onClick={analysisJob.retry}>重试失败项</Button>} />}

      {(demo || (analysisTerminal && summaryQuery.data?.terminal)) && <section className="entity-results">
        <div className="section-title-row">
          <div><h2>问题类型对照</h2><p>进入具体类型后查看真实差异、AI 成因与待执行治理方案。</p></div>
          <div className="section-title-actions"><span>{demo && selection.size > 0 ? `已选择 ${selection.size} 个问题` : `共 ${totalIssues} 个问题`}</span>{!demo && proposalReady > 0 && <Button type="primary" icon={<Sparkles size={15} />} onClick={() => setBatchOpen(true)}>AI 一键处理</Button>}</div>
        </div>
        {!demo && summaries.length === 0 ? <Empty description="AI 分析完成，未发现需要治理的问题" /> :
        <div className="entity-table" role="table" aria-label="问题类型对照">
          <div className="entity-row entity-header" role="row">
            <span>处理</span><span>问题类型</span><span>三方系统</span><span>希沃魔方</span><span>发现问题</span><span>状态</span><span />
          </div>
          {summaries.map((summary) => {
            const people = demo ? differencesFor(summary.type) : [];
            const issueIds = issueIdsFor(people);
            const state = getSelectionState(selection, issueIds);
            const canInspect = demo ? issueIds.length > 0 : analysisTerminal;
            return (
              <div className="entity-row" role="row" key={summary.type}>
                <Checkbox aria-label={`选择全部${summary.label}问题`} checked={state.checked} indeterminate={state.indeterminate} disabled={!demo || !canInspect} onChange={(event) => setSelection((current) => toggleCategory(current, people, event.target.checked))} />
                <button className="entity-name" type="button" disabled={!canInspect} onClick={() => navigate(`/tasks/${taskId}/differences/${summary.type}`)}>
                  <strong>{summary.label}</strong><small>{canInspect ? `${summary.issueCount} 个差异` : "等待差异检测"}</small>
                </button>
                <span className="source-value">{summary.sourceCount}</span>
                <span className="target-value">{summary.targetCount}</span>
                <strong className="issue-count">{canInspect ? summary.issueCount : "--"}</strong>
                <Tag color={canInspect ? "warning" : "default"}>{canInspect ? "待治理" : "待检测"}</Tag>
                <button className="row-arrow" aria-label={`查看${summary.label}问题`} type="button" disabled={!canInspect} onClick={() => navigate(`/tasks/${taskId}/differences/${summary.type}`)}><ArrowRight size={17} /></button>
              </div>
            );
          })}
        </div>}
      </section>}
      {!demo && analysisJobId && <BatchAnalysisModal open={batchOpen} taskId={taskId} jobId={analysisJobId} onClose={() => setBatchOpen(false)} onOpenEntityType={(entityType) => { setBatchOpen(false); navigate(`/tasks/${taskId}/differences/${entityType}`); }} />}
    </main>
  );
}

import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Progress, Skeleton, Tag } from "antd";
import { ArrowRight, Check, CircleDot, FileInput, RotateCcw, ScanSearch, Sparkles } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { queryKeys } from "../../api/queryKeys";
import { reconciliationApi, type WorkflowStage } from "../../api/reconciliation";
import { BackButton } from "../../components/BackButton";
import { demoEntitySummaries, differencesFor, entityLabels } from "../../data/demoDifferences";
import { findTask } from "../../data/taskHistory";
import type { EntitySummary } from "../../types/domain";
import { getSelectionState, issueIdsFor, toggleCategory } from "../differences/selection";
import { useIssueSelection } from "../differences/useIssueSelection";
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

export function TaskDetailPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const historyTask = findTask(taskId);
  const demo = Boolean(historyTask?.isDemo);
  const workflow = useReconciliationWorkflow(taskId, !demo);
  const { selection, setSelection } = useIssueSelection(taskId);
  const task = workflow.task.data;
  const canReadDifferences = Boolean(task && stageIndex[task.workflow.stage] >= 3);
  const differenceQuery = useQuery({
    queryKey: queryKeys.differences(taskId, { limit: 200 }),
    queryFn: ({ signal }) => reconciliationApi.listDifferences(taskId, { limit: 200 }, signal),
    enabled: !demo && canReadDifferences,
    refetchInterval: (query) => query.state.data?.items.some((item) => item.analysis_status === "pending") ? 2_000 : false,
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

  const selectedTypes = demo
    ? historyTask!.selectedEntityTypes
    : task?.entity_types ?? historyTask?.selectedEntityTypes ?? [];
  const realItems = differenceQuery.data?.items ?? [];
  const summaries: EntitySummary[] = demo
    ? demoEntitySummaries
    : selectedTypes.map((type) => ({
      type,
      label: entityLabels[type],
      sourceCount: historyTask?.entityCounts?.[type]?.source ?? 0,
      targetCount: historyTask?.entityCounts?.[type]?.target ?? 0,
      issueCount: realItems.filter((item) => item.entity_type === type).length,
    }));
  const totalIssues = demo ? historyTask!.issueCount : realItems.length;
  const taskTitle = historyTask?.title ?? `对账任务 ${taskId.slice(0, 8)}`;
  const sourceFile = historyTask?.sourceFile ?? "三方系统快照";
  const targetFile = historyTask?.targetFile ?? "希沃快照";
  const currentWorkflow = task?.workflow;
  const currentIndex = currentWorkflow ? stageIndex[currentWorkflow.stage] : 4;

  return (
    <main className="page-shell task-detail-page">
      <BackButton fallback="/tasks" label="返回任务列表" />
      <section className="detail-heading">
        <div>
          <span className="heading-tags">
            <Tag color={currentWorkflow?.status === "failed" ? "error" : currentWorkflow?.stage === "complete" || demo ? "success" : "processing"}>
              {currentWorkflow?.status === "failed" ? "处理失败" : currentWorkflow?.stage === "complete" || demo ? "分析完成" : "处理中"}
            </Tag>
            {demo && <Tag>演示差异</Tag>}
          </span>
          <h1>{taskTitle}</h1>
          <p>{sourceFile} <ArrowRight size={13} /> {targetFile}</p>
        </div>
        <div className="detail-total"><span>发现问题</span><strong>{canReadDifferences || demo ? totalIssues : "--"}</strong></div>
      </section>

      {workflow.task.isError && <Alert className="workflow-alert" type="error" showIcon message="任务状态读取失败" description={workflow.task.error.message} action={<Button size="small" onClick={() => void workflow.task.refetch()}>重试</Button>} />}
      {currentWorkflow?.status === "failed" && (
        <Alert className="workflow-alert" type="error" showIcon message={currentWorkflow.error?.message ?? "处理失败"} description={currentWorkflow.error?.code} action={workflow.canRetry ? <Button icon={<RotateCcw size={14} />} loading={workflow.retrying} onClick={workflow.retry}>重试当前阶段</Button> : undefined} />
      )}

      <section className="stage-track" aria-label="任务处理阶段">
        {stages.map((stage, index) => {
          const completed = demo || currentIndex > index;
          const active = !demo && currentIndex === index && currentWorkflow?.status !== "failed";
          const failed = !demo && currentIndex === index && currentWorkflow?.status === "failed";
          const Icon = stage.icon;
          const isAnalysis = stage.id === "analysis";
          let statusText = completed ? "已完成" : "等待处理";
          if (active) statusText = isAnalysis ? "AI 分析中" : "正在处理";
          if (failed) statusText = "处理失败";
          return (
            <div className={`stage${completed ? " completed" : ""}${active ? " active" : ""}${failed ? " failed" : ""}`} key={stage.id}>
              <span className="stage-icon">{completed ? <Check size={15} /> : <Icon className={active && isAnalysis ? "spin" : ""} size={15} />}</span>
              <span className="stage-copy"><strong>{stage.label}</strong><small>{statusText}</small></span>
              {active && isAnalysis && currentWorkflow && (
                <div className="stage-analysis-progress">
                  <span>已完成 {currentWorkflow.analysis.completed} / {currentWorkflow.analysis.total}</span>
                  <Progress percent={currentWorkflow.analysis.total ? Math.round(currentWorkflow.analysis.completed / currentWorkflow.analysis.total * 100) : 0} showInfo={false} size="small" />
                  <small>成功 {currentWorkflow.analysis.succeeded} · 仅人工 {currentWorkflow.analysis.manual_review} · 失败 {currentWorkflow.analysis.failed}</small>
                </div>
              )}
            </div>
          );
        })}
      </section>

      <section className="entity-results">
        <div className="section-title-row">
          <div><h2>问题类型对照</h2><p>进入具体类型后查看真实差异、AI 成因与待执行治理方案。</p></div>
          <span>{demo && selection.size > 0 ? `已选择 ${selection.size} 个问题` : canReadDifferences || demo ? `共 ${totalIssues} 个问题` : "等待差异检测"}</span>
        </div>
        <div className="entity-table" role="table" aria-label="问题类型对照">
          <div className="entity-row entity-header" role="row">
            <span>处理</span><span>问题类型</span><span>三方系统</span><span>希沃魔方</span><span>发现问题</span><span>状态</span><span />
          </div>
          {summaries.map((summary) => {
            const people = demo ? differencesFor(summary.type) : [];
            const issueIds = issueIdsFor(people);
            const state = getSelectionState(selection, issueIds);
            const canInspect = demo ? issueIds.length > 0 : canReadDifferences;
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
        </div>
      </section>
    </main>
  );
}

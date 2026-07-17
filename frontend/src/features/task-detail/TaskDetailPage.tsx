import { Checkbox, Tag } from "antd";
import { ArrowRight, Check, CircleDot, FileInput, ScanSearch, Sparkles } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { BackButton } from "../../components/BackButton";
import { demoEntitySummaries, differencesFor } from "../../data/demoDifferences";
import { findTask } from "../../data/taskHistory";
import type { EntitySummary } from "../../types/domain";
import { getSelectionState, issueIdsFor, toggleCategory } from "../differences/selection";
import { useIssueSelection } from "../differences/useIssueSelection";

const stages = [
  { label: "数据接入", icon: FileInput },
  { label: "实体解析", icon: ScanSearch },
  { label: "差异检测", icon: CircleDot },
  { label: "AI 分析", icon: Sparkles },
];

export function TaskDetailPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const task = findTask(taskId);
  const { selection, setSelection } = useIssueSelection(taskId);

  if (!task) {
    return (
      <main className="page-shell empty-page">
        <BackButton fallback="/tasks" label="返回任务列表" />
        <h1>没有找到这个任务</h1>
        <p>任务可能已被清理，或者当前浏览器没有保存它。</p>
      </main>
    );
  }

  const summaries: EntitySummary[] = task.isDemo
    ? demoEntitySummaries
    : task.selectedEntityTypes.map((type) => ({
      type,
      label: { organization_unit: "部门", class: "班级", teacher: "教师", student: "学生" }[type],
      sourceCount: task.entityCounts?.[type]?.source ?? 0,
      targetCount: task.entityCounts?.[type]?.target ?? 0,
      issueCount: 0,
    }));

  return (
    <main className="page-shell task-detail-page">
      <BackButton fallback="/tasks" label="返回任务列表" />
      <section className="detail-heading">
        <div>
          <span className="heading-tags">
            <Tag color="success">{task.status === "ready" ? "已完成" : "处理中"}</Tag>
            {task.isDemo && <Tag>演示差异</Tag>}
          </span>
          <h1>{task.title}</h1>
          <p>{task.sourceFile} <ArrowRight size={13} /> {task.targetFile}</p>
        </div>
        <div className="detail-total"><span>发现问题</span><strong>{task.isDemo ? task.issueCount : "--"}</strong></div>
      </section>

      <section className="stage-track" aria-label="任务处理阶段">
        {stages.map((stage, index) => {
          const completed = task.isDemo || index === 0;
          const Icon = stage.icon;
          return (
            <div className={completed ? "stage completed" : "stage"} key={stage.label}>
              <span className="stage-icon">{completed ? <Check size={15} /> : <Icon size={15} />}</span>
              <span><strong>{stage.label}</strong><small>{completed ? "已完成" : "等待处理"}</small></span>
            </div>
          );
        })}
      </section>

      <section className="entity-results">
        <div className="section-title-row">
          <div><h2>问题类型对照</h2><p>可直接选择整类问题，也可以进入后只勾选具体人员的某一项问题。</p></div>
          <span>{selection.size > 0 ? `已选择 ${selection.size} 个问题` : "尚未选择问题"}</span>
        </div>
        <div className="entity-table" role="table" aria-label="问题类型对照">
          <div className="entity-row entity-header" role="row">
            <span>处理</span><span>问题类型</span><span>三方系统</span><span>希沃魔方</span><span>发现问题</span><span>状态</span><span />
          </div>
          {summaries.map((summary) => {
            const people = differencesFor(summary.type);
            const issueIds = issueIdsFor(people);
            const state = getSelectionState(selection, issueIds);
            const canInspect = task.isDemo && issueIds.length > 0;
            return (
              <div className="entity-row" role="row" key={summary.type}>
                <Checkbox
                  aria-label={`选择全部${summary.label}问题`}
                  checked={state.checked}
                  indeterminate={state.indeterminate}
                  disabled={!canInspect}
                  onChange={(event) => setSelection((current) => toggleCategory(current, people, event.target.checked))}
                />
                <button className="entity-name" type="button" disabled={!canInspect} onClick={() => navigate(`/tasks/${task.id}/differences/${summary.type}`)}>
                  <strong>{summary.label}</strong>
                  <small>{canInspect ? `${people.length} 个相关实体` : "等待差异检测"}</small>
                </button>
                <span className="source-value">{summary.sourceCount}</span>
                <span className="target-value">{summary.targetCount}</span>
                <strong className="issue-count">{canInspect ? summary.issueCount : "--"}</strong>
                <Tag color={canInspect ? "warning" : "default"}>{canInspect ? "待确认" : "待检测"}</Tag>
                <button className="row-arrow" aria-label={`查看${summary.label}问题`} type="button" disabled={!canInspect} onClick={() => navigate(`/tasks/${task.id}/differences/${summary.type}`)}>
                  <ArrowRight size={17} />
                </button>
              </div>
            );
          })}
        </div>
      </section>
    </main>
  );
}

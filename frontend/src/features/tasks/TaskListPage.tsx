import { Button, Tag } from "antd";
import {
  ArrowRight,
  ChevronDown,
  CircleCheck,
  Clock3,
  Database,
  FileCheck2,
  FileSpreadsheet,
  Plus,
  RefreshCcw,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { allTasks, groupTasksByTargetSource } from "../../data/taskHistory";
import { agentApi } from "../../api/agent";
import { toTaskHistoryItem } from "../../data/taskHistory";
import { useTaskDeletion } from "./useTaskDeletion";

const statusCopy = {
  ready: { label: "已完成", color: "success" },
  processing: { label: "处理中", color: "processing" },
  terminated: { label: "已终止", color: "default" },
  failed: { label: "失败", color: "error" },
} as const;

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function TaskListPage() {
  const navigate = useNavigate();
  const [backendTasks, setBackendTasks] = useState<ReturnType<typeof allTasks>>();
  const deletion = useTaskDeletion((taskId) => {
    setBackendTasks((current) => current?.filter((task) => task.id !== taskId));
  });
  useEffect(() => {
    const controller = new AbortController();
    void agentApi.history(undefined, controller.signal)
      .then((page) => setBackendTasks(page.items.map(toTaskHistoryItem)))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  const tasks = backendTasks ?? allTasks();
  const groups = useMemo(() => groupTasksByTargetSource(tasks), [tasks]);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const operatorAdjustedGroups = useRef(false);
  const issueCount = tasks
    .filter((task) => task.status !== "ready")
    .reduce((sum, task) => sum + task.issueCount, 0);
  const syncTasks = tasks.filter((task) => task.taskKind !== "rollback");
  const completedSyncTasks = syncTasks.filter((task) => task.status === "ready").length;
  const operationSuccessRate = syncTasks.length
    ? `${Math.round((completedSyncTasks / syncTasks.length) * 100)}%`
    : "暂无数据";

  useEffect(() => {
    setExpandedGroups((current) => {
      const knownKeys = new Set(groups.map((group) => group.key));
      const next = new Set([...current].filter((key) => knownKeys.has(key)));
      if (
        !operatorAdjustedGroups.current
        && next.size === 0
        && groups[0]
      ) {
        next.add(groups[0].key);
      }
      return setsMatch(current, next) ? current : next;
    });
  }, [groups]);

  function toggleGroup(key: string) {
    operatorAdjustedGroups.current = true;
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <main className="page-shell task-list-page apple-page">
      <section className="page-heading">
        <div>
          <p className="eyebrow">WORKSPACE OVERVIEW · 当前加载历史</p>
          <h1>对账任务</h1>
          <p>从一组数据开始，查看组织实体解析和差异结果。指标仅统计当前加载的历史任务。</p>
        </div>
        <Button type="primary" icon={<Plus size={16} />} onClick={() => navigate("/tasks/new")}>
          外部数据同步
        </Button>
      </section>

      <section className="summary-band apple-metric-grid" aria-label="任务概览">
        <div><FileCheck2 size={18} /><span>历史任务</span><strong>{tasks.length}</strong></div>
        <div><CircleCheck size={18} /><span>已完成</span><strong>{tasks.filter((task) => task.status === "ready").length}</strong></div>
        <div><RefreshCcw size={18} /><span>待处理问题</span><strong>{issueCount}</strong></div>
        <div><CircleCheck size={18} /><span>治理操作成功率</span><strong>{operationSuccessRate}</strong></div>
      </section>

      <section className="task-list" aria-label="历史任务">
        <div className="section-title-row task-list-heading">
          <h2>最近任务</h2>
          <span>点击任意一行查看详情</span>
        </div>
        <div className="task-source-groups">
        {groups.map((group) => {
          const expanded = expandedGroups.has(group.key);
          const panelId = `task-source-${safeDomId(group.key)}`;
          return (
            <section className="task-source-group" key={group.key} aria-label={`${group.name}任务`}>
              <button
                className="task-source-toggle"
                type="button"
                aria-expanded={expanded}
                aria-controls={panelId}
                aria-label={`${expanded ? "收起" : "展开"}${group.name}任务列表`}
                onClick={() => toggleGroup(group.key)}
              >
                <span className="task-source-icon">
                  {group.kind === "database"
                    ? <Database size={17} />
                    : <FileSpreadsheet size={17} />}
                </span>
                <span className="task-source-main">
                  <strong>{group.name}</strong>
                  <small>{sourceKindLabel(group.kind)} · 最近活动于 {formatTime(group.lastActivityAt)}</small>
                </span>
                <span className="task-source-summary">
                  <strong>{group.taskCount} 个任务</strong>
                  <small>{groupStatusLabel(group)}</small>
                </span>
                <ChevronDown className={`task-source-chevron${expanded ? " is-open" : ""}`} size={18} />
              </button>
              {expanded && (
                <div className="task-source-tasks" id={panelId}>
                  {group.tasks.map((task) => {
                    const status = statusCopy[task.status];
                    return (
                      <div className="task-row-wrapper" key={task.id}>
                        <button
                          className="task-row"
                          type="button"
                          onClick={() => navigate(`/tasks/${task.id}`)}
                        >
                          <span className="task-state-dot" data-status={task.status} />
                          <span className="task-main">
                            <span className="task-title-line">
                              <span className="task-kind-badge">{task.taskKind === "rollback" ? "回滚" : "同步"}</span>
                              <strong>{task.title}</strong>
                            </span>
                            <span className="task-file-line">
                              {task.sourceFile} <ArrowRight size={13} /> {task.targetFile}
                            </span>
                          </span>
                          <span className="task-counts">
                            <span>三方 {task.sourceAccepted}</span>
                            <span>魔方 {task.targetAccepted}</span>
                            <strong>{task.issueCount} 个问题</strong>
                          </span>
                          <span className="task-meta">
                            <Tag color={status.color}>{status.label}</Tag>
                            <span><Clock3 size={13} /> {formatTime(task.createdAt)}</span>
                          </span>
                          <ArrowRight className="task-arrow" size={18} />
                        </button>
                        {task.deletionEligible !== false && (
                          <button
                            className="task-delete-button"
                            type="button"
                            aria-label={`删除${task.title}`}
                            title={`删除${task.title}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              deletion.requestDelete(task);
                            }}
                          >
                            <Trash2 size={16} />
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}
        </div>
      </section>
      {deletion.confirmation}
    </main>
  );
}

function sourceKindLabel(kind: "database" | "local" | "upload" | "unknown") {
  if (kind === "database") return "数据库";
  if (kind === "local") return "本地授权 CSV";
  if (kind === "upload") return "临时上传 CSV";
  return "未识别数据源";
}

function groupStatusLabel(group: ReturnType<typeof groupTasksByTargetSource>[number]) {
  if (group.processingCount) return `${group.processingCount} 个进行中`;
  if (group.failedCount) return `${group.failedCount} 个失败`;
  return "全部已结束";
}

function setsMatch(left: Set<string>, right: Set<string>) {
  return left.size === right.size && [...left].every((item) => right.has(item));
}

function safeDomId(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]/g, "-");
}

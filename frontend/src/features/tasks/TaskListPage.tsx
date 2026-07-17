import { Button, Tag } from "antd";
import { ArrowRight, CircleCheck, Clock3, FileCheck2, Plus, RefreshCcw } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { allTasks } from "../../data/taskHistory";

const statusCopy = {
  ready: { label: "已完成", color: "success" },
  processing: { label: "处理中", color: "processing" },
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
  const tasks = allTasks();
  const issueCount = tasks.reduce((sum, task) => sum + task.issueCount, 0);

  return (
    <main className="page-shell task-list-page">
      <section className="page-heading">
        <div>
          <p className="eyebrow">RECONCILIATION TASKS</p>
          <h1>对账任务</h1>
          <p>从一组数据开始，查看组织实体解析和差异结果。</p>
        </div>
        <Button type="primary" icon={<Plus size={16} />} onClick={() => navigate("/tasks/new")}>
          新建对账
        </Button>
      </section>

      <section className="summary-band" aria-label="任务概览">
        <div><FileCheck2 size={18} /><span>历史任务</span><strong>{tasks.length}</strong></div>
        <div><CircleCheck size={18} /><span>已完成</span><strong>{tasks.filter((task) => task.status === "ready").length}</strong></div>
        <div><RefreshCcw size={18} /><span>待处理问题</span><strong>{issueCount}</strong></div>
      </section>

      <section className="task-list" aria-label="历史任务">
        <div className="section-title-row">
          <h2>最近任务</h2>
          <span>点击任意一行查看详情</span>
        </div>
        {tasks.map((task) => {
          const status = statusCopy[task.status];
          return (
            <button
              className="task-row"
              key={task.id}
              type="button"
              onClick={() => navigate(`/tasks/${task.id}`)}
            >
              <span className="task-state-dot" data-status={task.status} />
              <span className="task-main">
                <span className="task-title-line">
                  <strong>{task.title}</strong>
                  {task.isDemo && <Tag bordered={false}>演示数据</Tag>}
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
          );
        })}
      </section>
    </main>
  );
}

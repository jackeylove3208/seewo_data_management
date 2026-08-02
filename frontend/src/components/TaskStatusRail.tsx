import {
  Check,
  ChevronLeft,
  ChevronRight,
  Circle,
  PanelRightClose,
  Pause,
} from "lucide-react";
import { useState, type ReactNode } from "react";

const COLLAPSED_KEY = "mofa-task-status-collapsed";

export interface TaskStatusStage {
  id: string;
  label: string;
  icon?: ReactNode;
}

export function TaskStatusRail({
  stages,
  currentIndex,
  idle = false,
  blocked = false,
  failed = false,
  terminationRequested = false,
}: {
  stages: TaskStatusStage[];
  currentIndex: number;
  idle?: boolean;
  blocked?: boolean;
  failed?: boolean;
  terminationRequested?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSED_KEY) === "true",
  );

  function toggleCollapsed() {
    setCollapsed((current) => {
      const next = !current;
      localStorage.setItem(COLLAPSED_KEY, String(next));
      return next;
    });
  }

  return (
    <aside
      className={`task-status-rail${collapsed ? " is-collapsed" : ""}${idle ? " is-idle" : ""}`}
      aria-label="任务处理状态"
    >
      <header className="task-status-rail-header">
        <div className="task-status-copy">
          <small>PROCESS</small>
          <strong>任务处理状态</strong>
        </div>
        <button
          type="button"
          aria-label={collapsed ? "展开任务处理状态" : "收起任务处理状态"}
          title={collapsed ? "展开任务处理状态" : "收起任务处理状态"}
          onClick={toggleCollapsed}
        >
          {collapsed
            ? <ChevronLeft size={17} />
            : <PanelRightClose size={17} />}
        </button>
      </header>
      <ol className="task-status-stage-list">
        {stages.map((stage, index) => {
          const status = index < currentIndex
            ? "completed"
            : index === currentIndex
              ? failed ? "failed" : blocked ? "blocked" : "active"
              : "waiting";
          const label = terminationRequested
            && ["generate_report", "report_restore"].includes(stage.id)
            ? "生成终止报告"
            : stage.label;
          const statusLabel = status === "completed"
            ? "已完成"
            : status === "active"
              ? "正在处理"
              : status === "blocked"
                ? "分析已暂停"
                : status === "failed"
                  ? "处理失败"
                : "等待处理";
          const fallbackIcon = status === "completed"
            ? <Check size={14} />
            : status === "blocked" || status === "failed"
              ? <Pause size={14} />
              : status === "active"
                ? <ChevronRight size={14} />
                : <Circle size={12} />;
          return (
            <li key={stage.id} data-status={status}>
              <span className="task-status-stage-icon" aria-hidden="true">
                {stage.icon ?? fallbackIcon}
              </span>
              <span className="task-status-copy">
                <strong>{label}</strong>
                <small>{statusLabel}</small>
              </span>
            </li>
          );
        })}
      </ol>
      {idle && <p className="task-status-idle">等待创建任务</p>}
    </aside>
  );
}

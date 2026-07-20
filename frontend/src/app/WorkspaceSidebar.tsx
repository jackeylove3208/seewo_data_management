import {
  Activity,
  ChevronLeft,
  ChevronRight,
  DatabaseZap,
  History,
  MessageSquarePlus,
  PanelLeftClose,
  RefreshCw,
  X,
} from "lucide-react";
import { useEffect, useState, type RefObject } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { ConnectionStatus } from "../components/ConnectionStatus";
import { allTasks, TASK_HISTORY_UPDATED_EVENT } from "../data/taskHistory";
import type { TaskStatus } from "../types/domain";

const COLLAPSED_KEY = "mofa-workspace-collapsed";
const RECENT_TASK_LIMIT = 8;

const statusLabels: Record<TaskStatus, string> = {
  ready: "已完成",
  processing: "处理中",
  failed: "失败",
};

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(new Date(value));
}

export function WorkspaceSidebar({
  mobileOpen,
  mobileMode = false,
  onMobileClose,
  closeButtonRef,
}: {
  mobileOpen: boolean;
  mobileMode?: boolean;
  onMobileClose: () => void;
  closeButtonRef?: RefObject<HTMLButtonElement | null>;
}) {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSED_KEY) === "true");
  const [tasks, setTasks] = useState(() => allTasks().slice(0, RECENT_TASK_LIMIT));

  useEffect(() => {
    const refresh = () => setTasks(allTasks().slice(0, RECENT_TASK_LIMIT));
    window.addEventListener(TASK_HISTORY_UPDATED_EVENT, refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener(TASK_HISTORY_UPDATED_EVENT, refresh);
      window.removeEventListener("storage", refresh);
    };
  }, []);

  useEffect(() => {
    if (mobileOpen) closeButtonRef?.current?.focus();
  }, [closeButtonRef, mobileOpen]);

  function toggleCollapsed() {
    setCollapsed((current) => {
      const next = !current;
      localStorage.setItem(COLLAPSED_KEY, String(next));
      return next;
    });
  }

  const className = ["workspace-sidebar", collapsed ? "is-collapsed" : "", mobileOpen ? "is-mobile-open" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <aside
      className={className}
      aria-label="对账工作区"
      aria-hidden={mobileMode && !mobileOpen ? true : undefined}
      inert={mobileMode && !mobileOpen ? true : undefined}
    >
      <nav className="workspace-navigation" aria-label="对账工作区">
        <div className="workspace-brand-row">
          <NavLink className="workspace-brand" to="/tasks" aria-label="魔方 AI 数据治理" onClick={onMobileClose}>
            <span className="workspace-brand-mark"><DatabaseZap size={19} /></span>
            <span className="workspace-label"><strong>魔方 AI 数据治理</strong><small>组织数据工作台</small></span>
          </NavLink>
          <button ref={closeButtonRef} className="workspace-mobile-close" type="button" aria-label="关闭导航" onClick={onMobileClose}><X size={19} /></button>
        </div>

        <div className="workspace-primary-actions" aria-label="主要操作">
          <button
            className="workspace-agent-entry"
            type="button"
            aria-label="新建对话，即将开放"
            title="新建对话，即将开放"
            disabled
          >
            <MessageSquarePlus size={18} />
            <span className="workspace-label workspace-command-copy">
              <strong>新建对话</strong>
              <small>即将开放</small>
            </span>
          </button>
          <NavLink className="workspace-new-task" to="/tasks/new" onClick={onMobileClose}>
            <RefreshCw size={18} />
            <span className="workspace-label">外部数据同步</span>
          </NavLink>
        </div>

        <section className="workspace-history" aria-label="最近任务">
          <div className="workspace-section-title">
            <span className="workspace-label">历史记录</span>
            <History size={15} />
          </div>
          <div className="workspace-history-list">
            {tasks.map((task) => (
              <NavLink
                className="workspace-history-item"
                key={task.id}
                to={`/tasks/${task.id}`}
                title={task.title}
                aria-label={`${task.title}，${statusLabels[task.status]}，${task.issueCount} 个问题`}
                onClick={onMobileClose}
              >
                <span className="history-status-dot" data-status={task.status} />
                <span className="workspace-label history-copy">
                  <strong>{task.title}</strong>
                  <small><span>{formatTime(task.createdAt)}</span><span>{statusLabels[task.status]}</span><span>{task.issueCount} 个问题</span></small>
                </span>
                <ChevronRight className="workspace-label" size={14} />
              </NavLink>
            ))}
          </div>
          <NavLink className="workspace-all-history" to="/tasks" onClick={onMobileClose}>
            <History size={16} /><span className="workspace-label">查看全部历史</span>
          </NavLink>
        </section>

        <div className="workspace-sidebar-footer">
          <div className="workspace-connection"><Activity size={15} /><span className="workspace-label"><ConnectionStatus /></span></div>
          <button
            className="workspace-collapse"
            type="button"
            title={collapsed ? "展开侧栏" : "收起侧栏"}
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
            onClick={toggleCollapsed}
          >
            {collapsed ? <ChevronRight size={18} /> : <><PanelLeftClose size={17} /><span className="workspace-label">收起侧栏</span><ChevronLeft className="workspace-label collapse-trailing" size={15} /></>}
          </button>
        </div>
      </nav>
      <span className="sr-only">当前路径：{location.pathname}</span>
    </aside>
  );
}

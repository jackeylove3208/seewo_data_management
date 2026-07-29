import {
  Activity,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Database,
  FileSpreadsheet,
  History,
  MessageSquarePlus,
  PanelLeftClose,
  Trash2,
  X,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { NavLink, useLocation } from "react-router-dom";

import { ConnectionStatus } from "../components/ConnectionStatus";
import {
  allTasks,
  groupTasksByTargetSource,
  TASK_HISTORY_UPDATED_EVENT,
  toTaskHistoryItem,
} from "../data/taskHistory";
import { agentApi } from "../api/agent";
import type { TaskStatus } from "../types/domain";
import { useTaskDeletion } from "../features/tasks/useTaskDeletion";
import appIcon from "../assets/mofa-app-icon.png";

const COLLAPSED_KEY = "mofa-workspace-collapsed";
const RECENT_TASK_LIMIT = 8;

const statusLabels: Record<TaskStatus, string> = {
  ready: "已完成",
  processing: "处理中",
  terminated: "已终止",
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
  const groups = useMemo(() => groupTasksByTargetSource(tasks), [tasks]);
  const currentTaskId = /^\/tasks\/([^/]+)$/.exec(location.pathname)?.[1];
  const currentGroupKey = groups.find((group) => (
    group.tasks.some((task) => task.id === currentTaskId)
  ))?.key;
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const operatorAdjustedGroups = useRef(false);
  const previousCurrentGroupKey = useRef<string | undefined>(undefined);
  const deletion = useTaskDeletion((taskId) => {
    setTasks((current) => current.filter((task) => task.id !== taskId));
  });

  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => {
      void agentApi.history(undefined, controller.signal)
        .then((page) => {
          setTasks(page.items.map(toTaskHistoryItem).slice(0, RECENT_TASK_LIMIT));
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setTasks(allTasks().slice(0, RECENT_TASK_LIMIT));
          }
        });
    };
    refresh();
    window.addEventListener(TASK_HISTORY_UPDATED_EVENT, refresh);
    window.addEventListener("storage", refresh);
    return () => {
      controller.abort();
      window.removeEventListener(TASK_HISTORY_UPDATED_EVENT, refresh);
      window.removeEventListener("storage", refresh);
    };
  }, [location.pathname]);

  useEffect(() => {
    if (previousCurrentGroupKey.current !== currentGroupKey) {
      operatorAdjustedGroups.current = false;
      previousCurrentGroupKey.current = currentGroupKey;
    }
    setExpandedGroups((current) => {
      const knownKeys = new Set(groups.map((group) => group.key));
      const next = new Set([...current].filter((key) => knownKeys.has(key)));
      const preferred = currentGroupKey ?? groups[0]?.key;
      if (!operatorAdjustedGroups.current && currentGroupKey) {
        next.add(currentGroupKey);
      } else if (
        !operatorAdjustedGroups.current
        && next.size === 0
        && preferred
      ) {
        next.add(preferred);
      }
      return setsMatch(current, next) ? current : next;
    });
  }, [currentGroupKey, groups]);

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

  function toggleGroup(key: string) {
    operatorAdjustedGroups.current = true;
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const className = ["workspace-sidebar", collapsed ? "is-collapsed" : "", mobileOpen ? "is-mobile-open" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <aside
      className={`${className} apple-sidebar`}
      aria-label="对账工作区"
      aria-hidden={mobileMode && !mobileOpen ? true : undefined}
      inert={mobileMode && !mobileOpen ? true : undefined}
    >
      <nav className="workspace-navigation" aria-label="对账工作区">
        <div className="workspace-brand-row">
          <NavLink className="workspace-brand" to="/tasks" aria-label="魔方 AI 数据治理" onClick={onMobileClose}>
            <span className="workspace-brand-mark"><img src={appIcon} alt="" /></span>
            <span className="workspace-label"><strong>魔方 AI 数据治理</strong><small>组织数据工作台</small></span>
          </NavLink>
          <button ref={closeButtonRef} className="workspace-mobile-close" type="button" aria-label="关闭导航" onClick={onMobileClose}><X size={19} /></button>
        </div>

        <div className="workspace-primary-actions" aria-label="主要操作">
          <NavLink
            className="workspace-agent-entry"
            to="/conversations/new"
            aria-label="新建对话"
            title="新建对话"
            onClick={onMobileClose}
          >
            <MessageSquarePlus size={18} />
            <span className="workspace-label">新建对话</span>
          </NavLink>
        </div>

        <section className="workspace-history" aria-label="最近任务">
          <div className="workspace-section-title">
            <span className="workspace-label">历史记录</span>
            <History size={15} />
          </div>
          <div className="workspace-history-list workspace-source-groups">
            {groups.map((group) => {
              const expanded = expandedGroups.has(group.key);
              const panelId = `workspace-source-${safeDomId(group.key)}`;
              return (
                <div className="workspace-source-group" key={group.key}>
                  <button
                    className="workspace-source-toggle"
                    type="button"
                    aria-expanded={expanded}
                    aria-controls={panelId}
                    aria-label={`${expanded ? "收起" : "展开"}${group.name}任务列表`}
                    onClick={() => toggleGroup(group.key)}
                  >
                    <span className="workspace-source-icon">
                      {group.kind === "database"
                        ? <Database size={13} />
                        : <FileSpreadsheet size={13} />}
                    </span>
                    <span className="workspace-label workspace-source-copy">
                      <strong>{group.name}</strong>
                      <small>{group.taskCount} 个任务</small>
                    </span>
                    <ChevronDown
                      className={`workspace-label workspace-source-chevron${expanded ? " is-open" : ""}`}
                      size={14}
                    />
                  </button>
                  {expanded && (
                    <div className="workspace-source-tasks" id={panelId}>
                      {group.tasks.map((task) => (
                        <div className="workspace-history-row" key={task.id}>
                          <NavLink
                            className="workspace-history-item"
                            to={`/tasks/${task.id}`}
                            title={task.title}
                            aria-label={`${task.title}，${statusLabels[task.status]}，${task.issueCount} 个问题`}
                            onClick={onMobileClose}
                          >
                            <span className="history-status-dot" data-status={task.status} />
                            <span className="workspace-label history-copy">
                              <strong><span className="history-task-kind">{task.taskKind === "rollback" ? "回滚" : "同步"}</span>{task.title}</strong>
                              <small><span>{formatTime(task.createdAt)}</span><span>{statusLabels[task.status]}</span><span>{task.issueCount} 个问题</span></small>
                            </span>
                            <ChevronRight className="workspace-label" size={14} />
                          </NavLink>
                          {task.deletionEligible !== false && (
                            <button
                              className="history-delete-button"
                              type="button"
                              aria-label={`删除${task.title}`}
                              title={`删除${task.title}`}
                              onClick={(event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                deletion.requestDelete(task);
                              }}
                            >
                              <Trash2 size={14} />
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <NavLink className="workspace-all-history" to="/executions" onClick={onMobileClose}>
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
      {deletion.confirmation}
      <span className="sr-only">当前路径：{location.pathname}</span>
    </aside>
  );
}

function setsMatch(left: Set<string>, right: Set<string>) {
  return left.size === right.size && [...left].every((item) => right.has(item));
}

function safeDomId(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]/g, "-");
}

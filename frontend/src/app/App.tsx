import { Menu } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { TaskListPage } from "../features/tasks/TaskListPage";
import { ConversationCreatePage } from "../features/task-create/ConversationCreatePage";
import { TaskCreatePage } from "../features/task-create/TaskCreatePage";
import { TaskDetailPage } from "../features/task-detail/TaskDetailPage";
import { DifferenceCategoryPage } from "../features/differences/DifferenceCategoryPage";
import { ExecutionHistoryPage } from "../features/executions/ExecutionHistoryPage";
import { ExecutionDetailPage } from "../features/executions/ExecutionDetailPage";
import { AgentReportPage } from "../features/reports/AgentReportPage";
import appIcon from "../assets/mofa-app-icon.png";
import { AppProviders } from "./providers";
import { WorkspaceSidebar } from "./WorkspaceSidebar";

function useMobileWorkspace() {
  const query = "(max-width: 980px)";
  const [mobile, setMobile] = useState(() => window.matchMedia?.(query).matches ?? false);

  useEffect(() => {
    const media = window.matchMedia?.(query);
    if (!media) return;
    const update = (event: MediaQueryListEvent) => setMobile(event.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return mobile;
}

function AppLayout() {
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const mobileMode = useMobileWorkspace();
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  function closeMobileSidebar() {
    const shouldRestoreFocus = mobileOpen;
    setMobileOpen(false);
    if (shouldRestoreFocus) queueMicrotask(() => menuButtonRef.current?.focus());
  }

  return (
    <div className="app-shell apple-workspace">
      <WorkspaceSidebar mobileOpen={mobileOpen} mobileMode={mobileMode} onMobileClose={closeMobileSidebar} closeButtonRef={closeButtonRef} />
      {mobileOpen && <button className="workspace-overlay" type="button" aria-label="关闭导航" onClick={closeMobileSidebar} />}
      <div className="workspace-main">
        <header className="workspace-mobile-header">
          <button ref={menuButtonRef} className="mobile-menu-button" type="button" aria-label="打开导航" onClick={() => setMobileOpen(true)}><Menu size={20} /></button>
          <span className="mobile-brand-mark"><img src={appIcon} alt="" /></span>
          <strong>魔方 AI 数据治理</strong>
        </header>
        <Routes>
          <Route path="/tasks" element={<TaskListPage />} />
          <Route path="/conversations/new" element={<ConversationCreatePage />} />
          <Route path="/tasks/new" element={<TaskCreatePage />} />
          <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
          <Route path="/tasks/:taskId/report" element={<AgentReportPage />} />
          <Route path="/tasks/:taskId/differences/:entityType" element={<DifferenceCategoryPage />} />
          <Route path="/executions" element={<ExecutionHistoryPage />} />
          <Route path="/executions/:executionId" element={<ExecutionDetailPage />} />
          <Route path="*" element={<Navigate to="/tasks" replace />} />
        </Routes>
      </div>
    </div>
  );
}

export function App() {
  return (
    <AppProviders>
      <AppLayout />
    </AppProviders>
  );
}

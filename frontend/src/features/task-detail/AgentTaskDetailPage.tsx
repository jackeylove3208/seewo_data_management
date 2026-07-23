import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Progress, Skeleton, Tag } from "antd";
import { FileInput, Flag, GitBranch, ShieldCheck, StopCircle } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { agentApi, type AgentPhase, type AgentTask } from "../../api/agent";
import { BackButton } from "../../components/BackButton";

const phases: Array<{ id: AgentPhase; label: string; icon: typeof FileInput }> = [
  { id: "ingest_and_normalize", label: "数据接入", icon: FileInput },
  { id: "analyze_batches", label: "Agent 分析与决策", icon: GitBranch },
  { id: "execute_and_verify", label: "治理执行", icon: ShieldCheck },
  { id: "generate_report", label: "报告与回滚", icon: Flag },
];

function phaseIndex(phase: AgentPhase) {
  if (phase === "terminal" || phase === "report_restore") return phases.length;
  const index = phases.findIndex((item) => item.id === phase);
  return index < 0 ? 0 : index;
}

export function AgentTaskDetailPage({ taskId, initialTask }: { taskId: string; initialTask?: AgentTask }) {
  const navigate = useNavigate();
  const [terminateError, setTerminateError] = useState<string>();
  const task = useQuery({
    queryKey: ["agent-task", taskId],
    queryFn: ({ signal }) => agentApi.task(taskId, signal),
    initialData: initialTask,
  });
  const events = useQuery({
    queryKey: ["agent-task-events", taskId],
    queryFn: ({ signal }) => agentApi.events(taskId, undefined, signal),
    enabled: Boolean(task.data),
    refetchInterval: 3000,
  });

  if (task.isLoading && !task.data) return <main className="page-shell task-detail-page"><BackButton fallback="/tasks" label="返回任务列表" /><Skeleton active paragraph={{ rows: 8 }} /></main>;
  if (task.isError || !task.data) return <main className="page-shell empty-page"><BackButton fallback="/tasks" label="返回任务列表" /><h1>没有找到这个 Agent 任务</h1><p>任务可能已被清理，或当前账号没有访问权限。</p></main>;

  const current = task.data;
  const terminal = ["completed", "terminated", "failed"].includes(current.status);
  const completed = phaseIndex(current.phase);
  async function terminate() {
    try {
      await agentApi.terminate(taskId);
      await task.refetch();
    } catch (error) {
      setTerminateError(error instanceof Error ? error.message : "终止任务失败");
    }
  }

  return (
    <main className="page-shell task-detail-page agent-task-detail-page">
      <BackButton fallback="/tasks" label="返回任务列表" />
      <section className="detail-heading"><div><span className="heading-tags"><Tag color={terminal ? "success" : "processing"}>{terminal ? "任务结束" : "处理中"}</Tag>{current.task_kind === "rollback" && <Tag color="warning">回滚任务</Tag>}</span><h1>{current.title ?? "Agent 数据同步任务"}</h1><p>后端持久化工作流 · {current.workflow_version}</p></div><div className="detail-total"><span>当前阶段</span><strong>{current.phase}</strong></div></section>
      {terminateError && <Alert type="error" showIcon message={terminateError} />}
      {!terminal && <div className="agent-task-actions"><Button danger icon={<StopCircle size={15} />} onClick={() => void terminate()}>终止任务</Button></div>}
      <section className="stage-track agent-stage-track" aria-label="Agent 任务处理阶段">
        {phases.map((phase, index) => { const Icon = phase.icon; const done = completed > index; const active = completed === index && !terminal; return <div className={`stage${done ? " completed" : ""}${active ? " active" : ""}`} key={phase.id}><span className="stage-icon"><Icon size={15} /></span><span className="stage-copy"><strong>{phase.label}</strong><small>{done ? "已完成" : active ? "正在处理" : "等待处理"}</small></span></div>; })}
      </section>
      {events.data?.events.length ? <section className="agent-event-history" aria-label="Agent 事件"><h2>任务事件</h2><ul>{events.data.events.slice().reverse().map((event) => <li key={event.id}><strong>{event.type}</strong><span>{event.created_at}</span></li>)}</ul></section> : <Progress percent={terminal ? 100 : Math.round((completed / phases.length) * 100)} showInfo={false} />}
      {current.report_id && <Button onClick={() => navigate(`/reports/${current.report_id}`)}>查看任务报告</Button>}
    </main>
  );
}

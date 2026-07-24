import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Input, Modal, Progress, Skeleton, Tag } from "antd";
import { Check, FileInput, Flag, GitBranch, RotateCcw, ShieldCheck, StopCircle, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  agentApi,
  type AgentGraphHumanGate,
  type AgentPhase,
  type AgentRollbackPreview,
  type AgentTask,
} from "../../api/agent";
import { BackButton } from "../../components/BackButton";
import { presentAgentEvent, presentAgentPhase } from "../agent-events/presentation";

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
  const [terminationLoading, setTerminationLoading] = useState(false);
  const [terminationGate, setTerminationGate] = useState<AgentGraphHumanGate>();
  const [rollbackLoading, setRollbackLoading] = useState(false);
  const [rollbackPreview, setRollbackPreview] = useState<AgentRollbackPreview>();
  const [gateLoading, setGateLoading] = useState<string>();
  const [clarificationMessage, setClarificationMessage] = useState("");
  const [clarificationDecisionId, setClarificationDecisionId] = useState<string>();
  const [clarificationInterpretation, setClarificationInterpretation] = useState<string>();
  const task = useQuery({
    queryKey: ["agent-task", taskId],
    queryFn: ({ signal }) => agentApi.task(taskId, signal),
    initialData: initialTask,
    refetchInterval: 3000,
  });
  const graph = useQuery({
    queryKey: ["agent-task-graph", taskId],
    queryFn: ({ signal }) => agentApi.graph(taskId, signal),
    enabled: task.data?.workflow_version === "agent-graph-v1",
    refetchInterval: 2000,
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
  const blocked = current.status === "blocked_model_error";
  const graphStageIndex = {
    data_ingestion: 0,
    agent_analysis: 1,
    governance_execution: 2,
    report_and_rollback: 3,
    terminal: phases.length,
  } as const;
  const completed = graph.data ? graphStageIndex[graph.data.business_stage] : phaseIndex(current.phase);
  const persistedTerminationGate = graph.data?.human_gates.find(
    (gate) => gate.status === "pending" && gate.kind === "termination_confirmation",
  );
  const activeTerminationGate = terminationGate ?? persistedTerminationGate;
  const pendingGates = graph.data?.human_gates.filter(
    (gate) => gate.status === "pending" && gate.kind !== "termination_confirmation",
  ) ?? [];
  const blockedEvent = blocked
    ? events.data?.events
      .slice()
      .reverse()
      .find(
        (event) =>
          event.type === "run.blocked_model_error"
          || event.type === "model_retry_exhausted",
      )
    : undefined;
  const blockedDescription = blockedEvent
    ? presentAgentEvent(blockedEvent).description
    : "模型处理未能完成。任务数据仍被安全保留，请终止任务后查看失败审计。";
  async function requestTermination() {
    setTerminationLoading(true);
    setTerminateError(undefined);
    try {
      if (current.workflow_version === "agent-graph-v1") {
        setTerminationGate(await agentApi.previewTermination(taskId));
      } else {
        await agentApi.terminate(taskId);
        await task.refetch();
      }
    } catch (error) {
      setTerminateError(error instanceof Error ? error.message : "终止任务失败");
    } finally {
      setTerminationLoading(false);
    }
  }

  async function decideTermination(decision: "approve" | "reject") {
    if (!activeTerminationGate) return;
    setTerminationLoading(true);
    setTerminateError(undefined);
    try {
      await agentApi.decideGraphGate(
        taskId,
        activeTerminationGate.id,
        decision,
        decision === "approve"
          ? "操作人确认终止当前任务"
          : "操作人取消终止当前任务",
      );
      setTerminationGate(undefined);
      await Promise.all([task.refetch(), graph.refetch(), events.refetch()]);
    } catch (error) {
      setTerminateError(error instanceof Error ? error.message : "终止确认未完成");
    } finally {
      setTerminationLoading(false);
    }
  }

  async function requestRollback() {
    setRollbackLoading(true);
    setTerminateError(undefined);
    try {
      const preview = await agentApi.previewRollback(taskId);
      setRollbackPreview(preview);
    } catch (error) {
      setTerminateError(error instanceof Error ? error.message : "创建回滚任务失败");
    } finally {
      setRollbackLoading(false);
    }
  }

  async function confirmRollback() {
    if (!rollbackPreview) return;
    setRollbackLoading(true);
    try {
      const rollbackTask = await agentApi.confirmRollback(rollbackPreview.task_id);
      setRollbackPreview(undefined);
      navigate(`/tasks/${rollbackTask.id}`);
    } catch (error) {
      setTerminateError(error instanceof Error ? error.message : "确认回滚任务失败");
    } finally {
      setRollbackLoading(false);
    }
  }

  async function rejectRollback() {
    if (!rollbackPreview || rollbackLoading) return;
    setRollbackLoading(true);
    try {
      await agentApi.rejectRollback(rollbackPreview.task_id);
      setRollbackPreview(undefined);
    } catch (error) {
      setTerminateError(error instanceof Error ? error.message : "取消回滚任务失败");
    } finally {
      setRollbackLoading(false);
    }
  }

  async function decideGate(gateId: string, decision: "approve" | "reject") {
    setGateLoading(gateId);
    setTerminateError(undefined);
    try {
      await agentApi.decideGraphGate(taskId, gateId, decision);
      await graph.refetch();
    } catch (error) {
      setTerminateError(error instanceof Error ? error.message : "审批操作未完成");
    } finally {
      setGateLoading(undefined);
    }
  }

  async function submitClarification(gateId: string) {
    const message = clarificationMessage.trim();
    if (!message || !agentApi.clarify) return;
    setGateLoading(gateId);
    setTerminateError(undefined);
    try {
      const interpretation = await agentApi.clarify(taskId, message);
      setClarificationInterpretation(interpretation.interpretation_zh);
      setClarificationDecisionId(
        interpretation.requires_second_confirmation
          ? interpretation.decision_id
          : undefined,
      );
    } catch (error) {
      setTerminateError(error instanceof Error ? error.message : "身份冲突说明未提交");
    } finally {
      setGateLoading(undefined);
    }
  }

  async function confirmClarification(gateId: string) {
    if (!clarificationDecisionId || !agentApi.confirmClarification) return;
    setGateLoading(gateId);
    setTerminateError(undefined);
    try {
      await agentApi.confirmClarification(taskId, clarificationDecisionId);
      setClarificationDecisionId(undefined);
      setClarificationInterpretation(undefined);
      setClarificationMessage("");
      await Promise.all([task.refetch(), graph.refetch(), events.refetch()]);
    } catch (error) {
      setTerminateError(error instanceof Error ? error.message : "身份冲突解释未确认");
    } finally {
      setGateLoading(undefined);
    }
  }

  return (
    <main className="page-shell task-detail-page agent-task-detail-page apple-page">
      <BackButton fallback="/tasks" label="返回任务列表" />
      <section className="detail-heading"><div><span className="heading-tags"><Tag color={terminal ? "success" : blocked ? "error" : "processing"}>{terminal ? "任务结束" : blocked ? "分析已暂停" : "处理中"}</Tag>{current.task_kind === "rollback" && <Tag color="warning">回滚任务</Tag>}</span><h1>{current.title ?? "Agent 数据同步任务"}</h1><p>后端持久化工作流 · {current.workflow_version}</p></div><div className="detail-total"><span>当前阶段</span><strong>{graph.data?.current_action_zh ?? presentAgentPhase(current.phase)}</strong></div></section>
      {terminateError && <Alert type="error" showIcon message={terminateError} />}
      {!terminal && <div className="agent-task-actions"><Button danger loading={terminationLoading} icon={<StopCircle size={15} />} onClick={() => void requestTermination()}>终止任务</Button></div>}
      {terminal && current.task_kind !== "rollback" && current.rollback_eligible && (
        <div className="agent-task-actions">
          <Button danger loading={rollbackLoading} icon={<RotateCcw size={15} />} onClick={() => void requestRollback()}>
            创建回滚任务
          </Button>
        </div>
      )}
      <section className="stage-track agent-stage-track" aria-label="Agent 任务处理阶段">
        {phases.map((phase, index) => { const Icon = phase.icon; const done = completed > index; const active = completed === index && !terminal && !blocked; const phaseBlocked = blocked && completed === index; return <div className={`stage${done ? " completed" : ""}${active ? " active" : ""}${phaseBlocked ? " blocked" : ""}`} key={phase.id}><span className="stage-icon"><Icon size={15} /></span><span className="stage-copy"><strong>{phase.label}</strong><small>{done ? "已完成" : phaseBlocked ? "分析已暂停" : active ? "正在处理" : "等待处理"}</small></span></div>; })}
      </section>
      {blocked && (
        <section className="agent-blocked-notice" aria-live="assertive">
          <div>
            <h2>模型分析已暂停</h2>
            <p>{blockedDescription}</p>
          </div>
        </section>
      )}
      {graph.data && !terminal && !blocked && (
        <section className="graph-live-progress" aria-live="polite">
          <span className="graph-orbit" aria-hidden="true"><i /><i /><i /></span>
          <div>
            <strong>{graph.data.current_action_zh}</strong>
            {graph.data.sub_agent_zh && graph.data.progress_total != null ? (
              <small>
                {graph.data.sub_agent_zh} · {graph.data.progress_completed ?? 0} / {graph.data.progress_total}
              </small>
            ) : null}
            <small>Agent 正在依据受控状态图安全处理，离开页面不会中断任务。</small>
          </div>
        </section>
      )}
      {pendingGates.map((gate) => gate.kind === "identity_conflict" ? (
        <section className="graph-approval-card graph-clarification-card" key={gate.id}>
          <div>
            <Tag color="warning">需要说明</Tag>
            <h2>需要人工判断身份冲突</h2>
            <p>共有 {gate.item_count} 条冲突证据无法由 Agent 安全决定。请说明这些记录的关系和期望处理方式；模型会先解释为受限决策，确认后才继续。</p>
          </div>
          <label htmlFor={`identity-clarification-${gate.id}`}>身份冲突处理说明</label>
          <Input.TextArea
            id={`identity-clarification-${gate.id}`}
            aria-label="身份冲突处理说明"
            value={clarificationMessage}
            rows={4}
            disabled={Boolean(clarificationDecisionId)}
            placeholder="例如：两条记录属于同一名学生，请保留编号 S-001。"
            onChange={(event) => setClarificationMessage(event.target.value)}
          />
          {clarificationInterpretation && (
            <Alert
              type={clarificationDecisionId ? "info" : "warning"}
              showIcon
              message={clarificationDecisionId ? "待确认的模型解释" : "请补充说明"}
              description={clarificationInterpretation}
            />
          )}
          <div className="graph-approval-actions">
            {clarificationDecisionId ? (
              <Button
                type="primary"
                icon={<Check size={14} />}
                loading={gateLoading === gate.id}
                onClick={() => void confirmClarification(gate.id)}
              >
                确认模型解释
              </Button>
            ) : (
              <Button
                type="primary"
                disabled={!clarificationMessage.trim()}
                loading={gateLoading === gate.id}
                onClick={() => void submitClarification(gate.id)}
              >
                提交说明
              </Button>
            )}
          </div>
        </section>
      ) : (
        <section className="graph-approval-card" key={gate.id}>
          <div><Tag color="warning">需要确认</Tag><h2>高风险操作审批</h2><p>同类问题已合并，共 {gate.item_count} 条记录。只有本组当前冻结内容会受到本次决定影响。</p></div>
          <div className="graph-approval-actions">
            <Button icon={<X size={14} />} loading={gateLoading === gate.id} onClick={() => void decideGate(gate.id, "reject")}>拒绝</Button>
            <Button type="primary" icon={<Check size={14} />} loading={gateLoading === gate.id} onClick={() => void decideGate(gate.id, "approve")}>同意</Button>
          </div>
        </section>
      ))}
      {events.data?.events.length ? (
        <section className="agent-event-history" aria-label="Agent 事件">
          <div className="agent-event-heading">
            <div><span>PROCESS</span><h2>任务处理记录</h2></div>
            <small>共 {events.data.events.length} 条</small>
          </div>
          <ol>
            {events.data.events.slice().reverse().map((event) => {
              const presented = presentAgentEvent(event);
              return (
                <li className={presented.tone} key={event.id}>
                  <span className="agent-event-dot" aria-hidden="true" />
                  <div>
                    <strong>{presented.title}</strong>
                    <p>{presented.description}</p>
                  </div>
                  {presented.time && <time dateTime={event.created_at}>{presented.time}</time>}
                </li>
              );
            })}
          </ol>
        </section>
      ) : !graph.data && !blocked && <Progress percent={terminal ? 100 : Math.round((completed / phases.length) * 100)} showInfo={false} />}
      {current.report_id && <Button onClick={() => navigate(`/tasks/${taskId}/report`)}>查看任务报告</Button>}
      <Modal
        rootClassName="apple-agent-modal"
        title="确认创建独立回滚任务？"
        open={Boolean(rollbackPreview)}
        okText="确认回滚"
        cancelText="暂不回滚"
        okButtonProps={{ danger: true }}
        confirmLoading={rollbackLoading}
        closable={!rollbackLoading}
        maskClosable={!rollbackLoading}
        onOk={() => void confirmRollback()}
        onCancel={() => void rejectRollback()}
      >
        <p>将根据 {rollbackPreview?.operation_count ?? 0} 条已验证变更生成补偿操作。回滚会重新锁定全校数据，并生成独立报告。</p>
      </Modal>
      <Modal
        rootClassName="apple-agent-modal"
        title="确认终止当前任务？"
        open={Boolean(activeTerminationGate)}
        okText="确认终止"
        cancelText="继续执行"
        okButtonProps={{ danger: true }}
        confirmLoading={terminationLoading}
        closable={!terminationLoading}
        maskClosable={false}
        onOk={() => void decideTermination("approve")}
        onCancel={() => void decideTermination("reject")}
      >
        <p>终止后不会启动新的处理动作，当前原子操作会安全结束；已经验证成功的数据修改不会自动回退，系统将生成终止报告。</p>
      </Modal>
    </main>
  );
}

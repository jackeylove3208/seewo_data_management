import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Input, Modal, Progress, Skeleton, Tag } from "antd";
import { Check, CircleCheck, FileInput, FileText, Flag, GitBranch, RotateCcw, ShieldCheck, StopCircle, X } from "lucide-react";
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

const approvalEntityLabels: Record<string, string> = {
  student: "学生",
  teacher: "教师",
  department: "部门",
};

const approvalOperationLabels: Record<string, string> = {
  create: "新增",
  update: "修改",
  delete: "删除",
  retain: "保留",
  skip: "跳过",
};

type ReviewDecision = "approved" | "rejected";

function ApprovalItemDetails({
  gate,
  reviewDecisions,
  reviewCompleted,
  onReview,
}: {
  gate: AgentGraphHumanGate;
  reviewDecisions?: Record<string, ReviewDecision>;
  reviewCompleted?: boolean;
  onReview?: (findingId: string, decision: ReviewDecision) => void;
}) {
  const items = gate.items ?? [];
  if (!items.length) return null;
  const operationLabel = approvalOperationLabels[gate.operation ?? ""] ?? "处理";

  return (
    <details className="graph-approval-details" open={items.length <= 3}>
      <summary>查看具体操作（{items.length} 条）</summary>
      <ol>
        {items.map((item) => {
          const entityLabel = approvalEntityLabels[item.entity_kind] ?? "记录";
          const entityName = item.entity_name || "未填写姓名";
          const number = item.entity_number ? `（编号 ${item.entity_number}）` : "";
          const sourceContext = item.source_row_number
            ? `希沃第 ${item.source_row_number} 行`
            : item.source_locator;
          return (
            <li key={item.finding_id}>
              <strong>{operationLabel}{entityLabel}：{entityName}{number}</strong>
              <small>
                {sourceContext}
                {item.class_name ? ` · ${item.class_name}` : ""}
              </small>
              <p>{item.analysis_zh}</p>
              <p className="graph-approval-solution">{item.solution_zh}</p>
              {item.changes.length > 0 && (
                <dl className="graph-approval-changes">
                  {item.changes.map((change) => (
                    <div key={change.field}>
                      <dt>{change.field_zh}</dt>
                      <dd>
                        <span>{change.before ?? "空值"}</span>
                        <b aria-hidden="true">→</b>
                        <span>{change.after ?? "空值"}</span>
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
              {onReview && (
                <div className="graph-item-review">
                  <span
                    className={`graph-item-review-status ${reviewDecisions?.[item.finding_id] ?? "approved"}`}
                  >
                    {reviewCompleted
                      ? `${reviewDecisions?.[item.finding_id] === "rejected" ? "已拒绝" : "已同意"}${entityName}`
                      : reviewDecisions?.[item.finding_id] === "rejected"
                        ? "已选择拒绝"
                        : "默认同意"}
                  </span>
                  {!reviewCompleted && (
                    <span className="graph-item-review-actions">
                      <Button
                        aria-label={`拒绝${entityName}`}
                        size="small"
                        icon={<X size={12} />}
                        onClick={() => onReview(item.finding_id, "rejected")}
                      >
                        拒绝
                      </Button>
                      <Button
                        aria-label={`同意${entityName}`}
                        size="small"
                        type={reviewDecisions?.[item.finding_id] === "rejected" ? "default" : "primary"}
                        icon={<Check size={12} />}
                        onClick={() => onReview(item.finding_id, "approved")}
                      >
                        同意
                      </Button>
                    </span>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </details>
  );
}

export function AgentTaskDetailPage({ taskId, initialTask }: { taskId: string; initialTask?: AgentTask }) {
  const navigate = useNavigate();
  const [terminateError, setTerminateError] = useState<string>();
  const [terminationLoading, setTerminationLoading] = useState(false);
  const [terminationGate, setTerminationGate] = useState<AgentGraphHumanGate>();
  const [rollbackLoading, setRollbackLoading] = useState(false);
  const [rollbackPreview, setRollbackPreview] = useState<AgentRollbackPreview>();
  const [gateLoading, setGateLoading] = useState<string>();
  const [gateDecisions, setGateDecisions] = useState<
    Partial<Record<string, "approved" | "rejected">>
  >({});
  const [gateItemDecisions, setGateItemDecisions] = useState<
    Record<string, Record<string, ReviewDecision>>
  >({});
  const [gateErrors, setGateErrors] = useState<Partial<Record<string, string>>>({});
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
  const terminationRequested = current.status === "terminated"
    || Boolean(graph.data?.termination_requested)
    || ["drain_current_atomic_unit", "termination_report"].includes(
      graph.data?.current_node ?? "",
    );
  const reportTitle = current.task_kind === "rollback"
    ? "回滚报告已生成"
    : terminationRequested
      ? "终止报告已生成"
      : "任务报告已生成";
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
  const visibleGates = graph.data?.human_gates.filter(
    (gate) =>
      gate.kind !== "termination_confirmation"
      && (
        gate.status === "pending"
        || (
          gate.kind === "high_risk_approval"
          && ["approved", "rejected"].includes(gate.status)
        )
      ),
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
    setGateErrors((currentErrors) => ({ ...currentErrors, [gateId]: "" }));
    try {
      const result = await agentApi.decideGraphGate(taskId, gateId, decision);
      setGateDecisions((currentDecisions) => ({
        ...currentDecisions,
        [gateId]: result.status,
      }));
      await Promise.all([task.refetch(), graph.refetch(), events.refetch()]);
    } catch (error) {
      setGateErrors((currentErrors) => ({
        ...currentErrors,
        [gateId]: error instanceof Error ? error.message : "审批操作未完成",
      }));
    } finally {
      setGateLoading(undefined);
    }
  }

  function reviewDecisionFor(gate: AgentGraphHumanGate, findingId: string) {
    return gateItemDecisions[gate.id]?.[findingId]
      ?? gate.member_decisions?.[findingId]
      ?? "approved";
  }

  function setGateItemDecision(
    gate: AgentGraphHumanGate,
    findingId: string,
    decision: ReviewDecision,
  ) {
    setGateItemDecisions((current) => ({
      ...current,
      [gate.id]: {
        ...Object.fromEntries(
          (gate.items ?? []).map((item) => [
            item.finding_id,
            current[gate.id]?.[item.finding_id]
              ?? gate.member_decisions?.[item.finding_id]
              ?? "approved",
          ]),
        ),
        [findingId]: decision,
      },
    }));
  }

  function setAllGateItemDecisions(
    gate: AgentGraphHumanGate,
    decision: ReviewDecision,
  ) {
    setGateItemDecisions((current) => ({
      ...current,
      [gate.id]: Object.fromEntries(
        (gate.items ?? []).map((item) => [item.finding_id, decision]),
      ),
    }));
  }

  async function submitMediumRiskReview(gate: AgentGraphHumanGate) {
    if (!gate.membership_hash || graph.data?.graph_cursor === undefined) {
      setGateErrors((currentErrors) => ({
        ...currentErrors,
        [gate.id]: "审核清单缺少版本信息，请刷新任务后重试",
      }));
      return;
    }
    const approvedFindingIds: string[] = [];
    const rejectedFindingIds: string[] = [];
    for (const item of gate.items ?? []) {
      if (reviewDecisionFor(gate, item.finding_id) === "rejected") {
        rejectedFindingIds.push(item.finding_id);
      } else {
        approvedFindingIds.push(item.finding_id);
      }
    }
    setGateLoading(gate.id);
    setGateErrors((currentErrors) => ({ ...currentErrors, [gate.id]: "" }));
    try {
      const result = await agentApi.decideGraphGate(
        taskId,
        gate.id,
        approvedFindingIds.length ? "approve" : "reject",
        "操作人完成中风险逐项复核",
        {
          approved_finding_ids: approvedFindingIds,
          rejected_finding_ids: rejectedFindingIds,
          graph_cursor: graph.data.graph_cursor,
          membership_hash: gate.membership_hash,
        },
      );
      setGateDecisions((currentDecisions) => ({
        ...currentDecisions,
        [gate.id]: result.status,
      }));
      await Promise.all([task.refetch(), graph.refetch(), events.refetch()]);
    } catch (error) {
      setGateErrors((currentErrors) => ({
        ...currentErrors,
        [gate.id]: error instanceof Error ? error.message : "中风险复核未完成",
      }));
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
      <section className="detail-heading"><div><span className="heading-tags"><Tag color={terminal ? "success" : blocked ? "error" : terminationRequested ? "warning" : "processing"}>{terminationRequested ? "任务已终止" : terminal ? "任务结束" : blocked ? "分析已暂停" : "处理中"}</Tag>{current.task_kind === "rollback" && <Tag color="warning">回滚任务</Tag>}</span><h1>{current.title ?? "Agent 数据同步任务"}</h1><p>后端持久化工作流 · {current.workflow_version}</p></div><div className="detail-total"><span>当前阶段</span><strong>{graph.data?.current_action_zh ?? presentAgentPhase(current.phase)}</strong></div></section>
      {terminationRequested && !current.report_id && (
        <section className="agent-termination-notice" aria-live="polite">
          <span className="agent-summary-icon" aria-hidden="true"><FileText size={20} /></span>
          <div>
            <h2>任务已终止</h2>
            <p>治理执行已停止，仍在为你生成终止报告。已完成的修改将被保留，未开始的操作不会继续。</p>
          </div>
        </section>
      )}
      {current.report_id && (
        <section className="agent-report-summary-card">
          <span className="agent-summary-icon" aria-hidden="true"><CircleCheck size={20} /></span>
          <div className="agent-report-summary-copy">
            <Tag color="success">{current.status === "terminated" ? "已终止" : "已完成"}</Tag>
            <h2>{reportTitle}</h2>
            <p>报告已保存任务事实、治理结果和可用的回滚依据。</p>
          </div>
          <Button type="primary" icon={<FileText size={15} />} onClick={() => navigate(`/tasks/${taskId}/report`)}>
            查看任务报告
          </Button>
        </section>
      )}
      {terminateError && <Alert type="error" showIcon message={terminateError} />}
      {!terminal && !terminationRequested && <div className="agent-task-actions"><Button danger loading={terminationLoading} icon={<StopCircle size={15} />} onClick={() => void requestTermination()}>终止任务</Button></div>}
      {terminal && current.task_kind !== "rollback" && current.rollback_eligible && (
        <div className="agent-task-actions">
          <Button danger loading={rollbackLoading} icon={<RotateCcw size={15} />} onClick={() => void requestRollback()}>
            创建回滚任务
          </Button>
        </div>
      )}
      <section className="stage-track agent-stage-track" aria-label="Agent 任务处理阶段">
        {phases.map((phase, index) => { const Icon = phase.icon; const done = completed > index; const active = completed === index && !terminal && !blocked; const phaseBlocked = blocked && completed === index; const phaseLabel = terminationRequested && phase.id === "generate_report" ? "生成终止报告" : phase.label; return <div className={`stage${done ? " completed" : ""}${active ? " active" : ""}${phaseBlocked ? " blocked" : ""}`} key={phase.id}><span className="stage-icon"><Icon size={15} /></span><span className="stage-copy"><strong>{phaseLabel}</strong><small>{done ? "已完成" : phaseBlocked ? "分析已暂停" : active ? "正在处理" : "等待处理"}</small></span></div>; })}
      </section>
      {blocked && (
        <section className="agent-blocked-notice" aria-live="assertive">
          <div>
            <h2>模型分析已暂停</h2>
            <p>{blockedDescription}</p>
          </div>
        </section>
      )}
      {graph.data && !terminal && !blocked && !terminationRequested && (
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
      {visibleGates.map((gate) => gate.kind === "identity_conflict" ? (
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
        <section
          className={`graph-approval-card graph-approval-${gateDecisions[gate.id] ?? gate.status}`}
          key={gate.id}
        >
          <div className="graph-approval-main">
            {gate.risk === "medium" && (gateDecisions[gate.id] ?? gate.status) === "pending" ? (
              <Tag color="processing">中风险 · 默认同意</Tag>
            ) : (gateDecisions[gate.id] ?? gate.status) === "approved" ? (
              <Tag color="success">已允许</Tag>
            ) : (gateDecisions[gate.id] ?? gate.status) === "rejected" ? (
              <Tag color="error">已拒绝</Tag>
            ) : (
              <Tag color="warning">需要确认</Tag>
            )}
            <h2>{gate.summary_zh ?? "治理操作审核"}</h2>
            {gate.risk_reason_zh && <p>{gate.risk_reason_zh}</p>}
            <p>同类问题已合并，共 {gate.item_count} 条记录。只有本组当前冻结内容会受到本次决定影响。</p>
            <ApprovalItemDetails
              gate={gate}
              reviewCompleted={(gateDecisions[gate.id] ?? gate.status) !== "pending"}
              reviewDecisions={
                gate.risk === "medium"
                  ? Object.fromEntries(
                    (gate.items ?? []).map((item) => [
                      item.finding_id,
                      reviewDecisionFor(gate, item.finding_id),
                    ]),
                  )
                  : undefined
              }
              onReview={
                gate.risk === "medium"
                  ? (findingId, decision) => setGateItemDecision(gate, findingId, decision)
                  : undefined
              }
            />
            {(gateDecisions[gate.id] ?? gate.status) === "pending"
              && gate.actionable === false && (
              <Alert
                type="warning"
                showIcon
                message="审批不可用"
                description={gate.unavailable_reason_zh ?? "该审批不属于任务当前执行节点。"}
              />
            )}
          </div>
          {gateErrors[gate.id] && (
            <Alert type="error" showIcon message={gateErrors[gate.id]} />
          )}
          {(gateDecisions[gate.id] ?? gate.status) === "pending"
            && gate.actionable !== false && (
            gate.risk === "medium" ? (
              <div className="graph-approval-actions graph-medium-review-actions">
                <Button onClick={() => setAllGateItemDecisions(gate, "rejected")}>全部拒绝</Button>
                <Button onClick={() => setAllGateItemDecisions(gate, "approved")}>全部同意</Button>
                <Button
                  type="primary"
                  loading={gateLoading === gate.id}
                  onClick={() => void submitMediumRiskReview(gate)}
                >
                  按当前选择继续
                </Button>
              </div>
            ) : (
              <div className="graph-approval-actions">
                <Button icon={<X size={14} />} loading={gateLoading === gate.id} onClick={() => void decideGate(gate.id, "reject")}>拒绝</Button>
                <Button type="primary" icon={<Check size={14} />} loading={gateLoading === gate.id} onClick={() => void decideGate(gate.id, "approve")}>同意</Button>
              </div>
            )
          )}
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

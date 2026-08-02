import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Modal, Progress, Skeleton, Tag } from "antd";
import { Check, CircleCheck, FileInput, FileText, Flag, GitBranch, RotateCcw, ShieldCheck, StopCircle, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  agentApi,
  type AgentClarificationSubmission,
  type AgentGraphApprovalItem,
  type AgentGraphHumanGate,
  type AgentGraphProgress,
  type AgentPhase,
  type AgentRollbackPreview,
  type AgentTask,
} from "../../api/agent";
import { ApiError } from "../../api/client";
import { BackButton } from "../../components/BackButton";
import { IdentityConflictClarificationCard } from "../../components/IdentityConflictClarificationCard";
import { TaskStatusRail } from "../../components/TaskStatusRail";
import { advanceToNextPendingRiskHeading } from "../agent-approvals/advanceToNextRisk";
import { presentAgentEvent, presentAgentPhase } from "../agent-events/presentation";

const syncPhases: Array<{ id: AgentPhase; label: string; icon: typeof FileInput }> = [
  { id: "ingest_and_normalize", label: "数据接入", icon: FileInput },
  { id: "analyze_batches", label: "Agent 分析与决策", icon: GitBranch },
  { id: "execute_and_verify", label: "治理执行", icon: ShieldCheck },
  { id: "generate_report", label: "报告生成", icon: Flag },
];

const rollbackPhases: Array<{ id: AgentPhase; label: string; icon: typeof FileInput }> = [
  { id: "plan_restore", label: "读取并比对当前数据", icon: FileInput },
  { id: "clarify_restore_conflicts", label: "评估回滚影响", icon: GitBranch },
  { id: "approve_restore", label: "确认回滚范围", icon: Check },
  { id: "execute_restore", label: "执行与验证", icon: ShieldCheck },
  { id: "report_restore", label: "生成回滚报告", icon: Flag },
];

const rollbackCycleLockedMessage = "已经回滚，若想再次回滚，需下次同步后执行。";

function phaseIndex(
  phase: AgentPhase,
  taskPhases: Array<{ id: AgentPhase }>,
) {
  if (phase === "terminal") return taskPhases.length;
  const index = taskPhases.findIndex((item) => item.id === phase);
  return index < 0 ? 0 : index;
}

function rollbackNodeIndex(node: string, fallbackPhase: AgentPhase) {
  const indexByNode: Record<string, number> = {
    rollback_intent_confirmed: 0,
    acquire_school_lock: 0,
    load_verified_mutations: 0,
    assess_restore_impact: 1,
    wait_restore_conflicts: 1,
    wait_rollback_approval: 2,
    compile_restore_plan: 2,
    preflight_restore: 2,
    execute_restore_operations: 3,
    verify_restore_operations: 3,
    generate_rollback_report: 4,
    terminal: rollbackPhases.length,
  };
  return indexByNode[node] ?? phaseIndex(fallbackPhase, rollbackPhases);
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

function ApprovalItemRow({
  gate,
  item,
  reviewDecision,
  reviewCompleted,
  onReview,
}: {
  gate: AgentGraphHumanGate;
  item: AgentGraphApprovalItem;
  reviewDecision?: ReviewDecision;
  reviewCompleted?: boolean;
  onReview?: (findingId: string, decision: ReviewDecision) => void;
}) {
  const operationLabel = approvalOperationLabels[gate.operation ?? ""] ?? "处理";
  const entityLabel = approvalEntityLabels[item.entity_kind] ?? "记录";
  const itemActionLabel = gate.kind.startsWith("rollback_")
    ? item.operation_zh
    : `${operationLabel}${entityLabel}`;
  const entityName = item.entity_name || "未填写姓名";
  const number = item.entity_number ? `（编号 ${item.entity_number}）` : "";
  const sourceContext = item.source_row_number
    ? `希沃第 ${item.source_row_number} 行`
    : item.source_locator;
  const optIn = isOptInItem(item);
  const decision = reviewDecision ?? (optIn ? "rejected" : "approved");
  const checked = optIn ? decision === "approved" : decision === "rejected";

  return (
    <li>
      <strong>{itemActionLabel}：{entityName}{number}</strong>
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
      {reviewDecision && (
        <div className="graph-item-review">
          <span className={`graph-item-review-status ${decision}`}>
            {reviewCompleted
              ? optIn
                ? `${decision === "approved" ? "已选择执行" : "已保留"}${entityName}`
                : `${decision === "rejected" ? "已拒绝" : "已同意"}${entityName}`
              : optIn
                ? decision === "approved"
                  ? "已选择执行"
                  : "默认保留"
                : decision === "rejected"
                  ? "已选择拒绝"
                  : "默认同意"}
          </span>
          {!reviewCompleted && onReview && (
            <Checkbox
              aria-label={
                optIn
                  ? optInReviewLabel(item, entityName)
                  : `拒绝${entityName}`
              }
              checked={checked}
              onChange={(event) => onReview(
                item.finding_id,
                optIn
                  ? event.target.checked ? "approved" : "rejected"
                  : event.target.checked ? "rejected" : "approved",
              )}
            >
              {optIn
                ? item.changes.length === 1
                  ? "将班级设置为空"
                  : "同意整项变更（包括清空班级）"
                : "拒绝此项"}
            </Checkbox>
          )}
        </div>
      )}
    </li>
  );
}

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

  return (
    <details className="graph-approval-details" open={items.length <= 3}>
      <summary>查看具体操作（{items.length} 条）</summary>
      <ol>
        {items.map((item) => (
          <ApprovalItemRow
            gate={gate}
            item={item}
            key={item.finding_id}
            reviewDecision={reviewDecisions?.[item.finding_id]}
            reviewCompleted={reviewCompleted}
            onReview={onReview}
          />
        ))}
      </ol>
    </details>
  );
}

interface MediumReviewEntry {
  gate: AgentGraphHumanGate;
  item: AgentGraphApprovalItem;
}

interface MediumReviewGroup {
  key: string;
  entityKind: string;
  operation: string;
  summaries: string[];
  entries: MediumReviewEntry[];
}

function groupMediumRiskItems(gates: AgentGraphHumanGate[]): MediumReviewGroup[] {
  const groups = new Map<string, MediumReviewGroup>();
  const seenFindingIds = new Set<string>();
  for (const gate of gates) {
    const entityKind = gate.entity_kind ?? "record";
    const operation = gate.operation ?? "update";
    const key = `${entityKind}:${operation}`;
    const group = groups.get(key) ?? {
      key,
      entityKind,
      operation,
      summaries: [],
      entries: [],
    };
    if (gate.summary_zh && !group.summaries.includes(gate.summary_zh)) {
      group.summaries.push(gate.summary_zh);
    }
    for (const item of gate.items ?? []) {
      if (seenFindingIds.has(item.finding_id)) continue;
      seenFindingIds.add(item.finding_id);
      group.entries.push({ gate, item });
    }
    groups.set(key, group);
  }
  return [...groups.values()];
}

function mediumReviewGroupTitle(group: MediumReviewGroup) {
  const operation = approvalOperationLabels[group.operation] ?? "处理";
  const entity = approvalEntityLabels[group.entityKind] ?? "记录";
  return `${operation} ${group.entries.length} 条${entity}记录`;
}

function isOptInItem(item: AgentGraphApprovalItem) {
  return item.selection_mode === "opt_in";
}

function optInReviewLabel(item: AgentGraphApprovalItem, entityName: string) {
  return item.changes.length === 1 && item.changes[0]?.field === "class_name"
    ? `将${entityName}的班级设置为空`
    : `同意${entityName}的整项变更（包括将班级设置为空）`;
}

function reviewDecisionForGateState(
  gate: AgentGraphHumanGate,
  item: AgentGraphApprovalItem,
  decisions: Record<string, Record<string, ReviewDecision>>,
) {
  return decisions[gate.id]?.[item.finding_id]
    ?? gate.member_decisions?.[item.finding_id]
    ?? (isOptInItem(item) ? "rejected" : "approved");
}

function updateConflictSubmission(
  current: AgentGraphProgress | undefined,
  clarificationId: string,
  submission: AgentClarificationSubmission | null,
) {
  if (!current) return current;
  return {
    ...current,
    human_gates: current.human_gates.map((gate) => ({
      ...gate,
      conflicts: gate.conflicts?.map((conflict) => (
        conflict.clarification_id === clarificationId
          ? {
              ...conflict,
              status: submission ? "interpreted" : "pending",
              interpretation_zh: submission?.interpretation_zh ?? null,
              operator_submission: submission,
            }
          : conflict
      )),
    })),
  };
}

export function AgentTaskDetailPage({ taskId, initialTask }: { taskId: string; initialTask?: AgentTask }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [terminateError, setTerminateError] = useState<string>();
  const [terminationLoading, setTerminationLoading] = useState(false);
  const [terminationGate, setTerminationGate] = useState<AgentGraphHumanGate>();
  const [dismissedTerminationGateId, setDismissedTerminationGateId] = useState<string>();
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
  const [confirmedClarificationIds, setConfirmedClarificationIds] = useState<string[]>([]);
  const [clarificationConfirmedNotice, setClarificationConfirmedNotice] = useState(false);
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

  if (task.isLoading && !task.data) return <main className="page-shell task-detail-page apple-page"><BackButton fallback="/tasks" label="返回任务列表" /><Skeleton active paragraph={{ rows: 8 }} /></main>;
  if (task.isError || !task.data) return <main className="page-shell empty-page apple-page"><BackButton fallback="/tasks" label="返回任务列表" /><h1>没有找到这个 Agent 任务</h1><p>任务可能已被清理，或当前账号没有访问权限。</p></main>;

  const current = task.data;
  const terminal = ["completed", "terminated", "failed"].includes(current.status);
  const failed = current.status === "failed";
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
  const activePhases = current.task_kind === "rollback"
    ? rollbackPhases
    : syncPhases;
  const graphStageIndex = {
    data_ingestion: 0,
    agent_analysis: 1,
    governance_execution: 2,
    report_and_rollback: 3,
    terminal: syncPhases.length,
  } as const;
  const completed = graph.data
    ? current.task_kind === "rollback"
      ? rollbackNodeIndex(graph.data.current_node, current.phase)
      : graphStageIndex[graph.data.business_stage]
    : phaseIndex(current.phase, activePhases);
  const persistedTerminationGate = graph.data?.human_gates.find(
    (gate) =>
      gate.status === "pending"
      && gate.kind === "termination_confirmation"
      && gate.id !== dismissedTerminationGateId,
  );
  const activeTerminationGate = terminationGate ?? persistedTerminationGate;
  const visibleGates = graph.data?.human_gates.filter(
    (gate) =>
      gate.kind !== "termination_confirmation"
      && !(
        gate.kind === "identity_conflict"
        && gate.conflicts?.length
        && gate.conflicts.every((conflict) =>
          confirmedClarificationIds.includes(conflict.clarification_id)
        )
      )
      && (
        gate.status === "pending"
        || (
          gate.kind === "high_risk_approval"
          && ["approved", "rejected"].includes(gate.status)
        )
      ),
  ) ?? [];
  const mediumGates = visibleGates.filter(
    (gate) => gate.kind === "high_risk_approval" && gate.risk === "medium",
  );
  const otherGates = visibleGates.filter(
    (gate) => !(gate.kind === "high_risk_approval" && gate.risk === "medium"),
  );
  const mediumReviewGroups = groupMediumRiskItems(mediumGates);
  const pendingMediumGates = mediumGates.filter(
    (gate) => (gateDecisions[gate.id] ?? gate.status) === "pending",
  );
  const pendingMediumEntries = pendingMediumGates.flatMap((gate) =>
    (gate.items ?? []).map((item) => ({ gate, item })),
  );
  const pendingMediumFindingIds = new Set(
    pendingMediumEntries.map(({ item }) => item.finding_id),
  );
  const rejectedMediumFindingIds = new Set(
    pendingMediumEntries
      .filter(({ gate, item }) =>
        reviewDecisionForGateState(gate, item, gateItemDecisions) === "rejected"
      )
      .map(({ item }) => item.finding_id),
  );
  const hasMediumOptIn = mediumReviewGroups.some((group) =>
    group.entries.some(({ item }) => isOptInItem(item)),
  );
  const approvedMediumCount =
    pendingMediumFindingIds.size - rejectedMediumFindingIds.size;
  const mediumSubmitLabel = rejectedMediumFindingIds.size > 0
    ? `按当前选择继续（同意 ${approvedMediumCount}，拒绝 ${rejectedMediumFindingIds.size}）`
    : "全部同意并继续";
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
    setDismissedTerminationGateId(undefined);
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
      if (error instanceof ApiError && error.status === 409) {
        setDismissedTerminationGateId(activeTerminationGate.id);
        setTerminationGate(undefined);
        await Promise.allSettled([
          task.refetch(),
          graph.refetch(),
          events.refetch(),
        ]);
      }
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

  function dismissRollbackPreview() {
    if (rollbackLoading) return;
    setRollbackPreview(undefined);
  }

  function openExistingRollback() {
    if (!rollbackPreview) return;
    const rollbackTaskId = rollbackPreview.task_id;
    setRollbackPreview(undefined);
    navigate(`/tasks/${rollbackTaskId}`);
  }

  async function decideGate(
    gate: AgentGraphHumanGate,
    decision: "approve" | "reject",
  ) {
    const gateId = gate.id;
    const items = gate.items ?? [];
    const graphCursor = graph.data?.graph_cursor;
    const requiresFrozenReview = gate.kind === "high_risk_approval";
    if (
      requiresFrozenReview
      && (
        graphCursor === undefined
        || !gate.membership_hash
        || items.length === 0
      )
    ) {
      setGateErrors((currentErrors) => ({
        ...currentErrors,
        [gateId]: "审批清单缺少完整版本信息，请刷新任务后重试",
      }));
      return;
    }
    const findingIds = items.map((item) => item.finding_id);
    setGateLoading(gateId);
    setTerminateError(undefined);
    setGateErrors((currentErrors) => ({ ...currentErrors, [gateId]: "" }));
    try {
      const result = await agentApi.decideGraphGate(
        taskId,
        gateId,
        decision,
        decision === "approve"
          ? "操作人确认高风险治理操作"
          : "操作人拒绝高风险治理操作",
        requiresFrozenReview
          ? {
            approved_finding_ids: decision === "approve" ? findingIds : [],
            rejected_finding_ids: decision === "reject" ? findingIds : [],
            graph_cursor: graphCursor!,
            membership_hash: gate.membership_hash!,
          }
          : undefined,
      );
      setGateDecisions((currentDecisions) => ({
        ...currentDecisions,
        [gateId]: result.status,
      }));
      if (requiresFrozenReview) {
        advanceToNextPendingRiskHeading(gateId);
      }
      await Promise.all([task.refetch(), graph.refetch(), events.refetch()]);
    } catch (error) {
      const rawMessage = error instanceof Error ? error.message : "审批操作未完成";
      const message = rawMessage.toLowerCase().includes("stale")
        ? "审批清单已更新，请查看刷新后的操作后重新确认"
        : rawMessage;
      setGateErrors((currentErrors) => ({
        ...currentErrors,
        [gateId]: message,
      }));
      if (rawMessage.toLowerCase().includes("stale")) {
        await graph.refetch();
      }
    } finally {
      setGateLoading(undefined);
    }
  }

  function reviewDecisionFor(gate: AgentGraphHumanGate, findingId: string) {
    const item = (gate.items ?? []).find(
      (candidate) => candidate.finding_id === findingId,
    );
    return item
      ? reviewDecisionForGateState(gate, item, gateItemDecisions)
      : "approved";
  }

  function setMediumItemDecision(
    findingId: string,
    decision: ReviewDecision,
  ) {
    setGateItemDecisions((current) => {
      const next = { ...current };
      for (const gate of pendingMediumGates) {
        if (!(gate.items ?? []).some((item) => item.finding_id === findingId)) {
          continue;
        }
        next[gate.id] = {
          ...Object.fromEntries(
            (gate.items ?? []).map((item) => [
              item.finding_id,
              reviewDecisionForGateState(gate, item, current),
            ]),
          ),
          [findingId]: decision,
        };
      }
      return next;
    });
  }

  async function submitMediumRiskReviews() {
    const pendingGates = mediumGates.filter(
      (gate) => (gateDecisions[gate.id] ?? gate.status) === "pending",
    );
    if (!pendingGates.length) return;
    const graphCursor = graph.data?.graph_cursor;
    const invalidGate = pendingGates.find(
      (gate) =>
        gate.actionable === false
        || !gate.membership_hash
        || !(gate.items ?? []).length,
    );
    if (graphCursor === undefined || invalidGate) {
      const gateId = invalidGate?.id ?? pendingGates[0].id;
      setGateErrors((currentErrors) => ({
        ...currentErrors,
        [gateId]: invalidGate?.unavailable_reason_zh
          ?? "审核清单缺少完整版本信息，请刷新任务后重试",
      }));
      return;
    }
    setGateLoading("medium-risk-bulk-review");
    try {
      const decisions = pendingGates.map((gate) => {
        const approvedFindingIds: string[] = [];
        const rejectedFindingIds: string[] = [];
        for (const item of gate.items ?? []) {
          if (reviewDecisionForGateState(gate, item, gateItemDecisions) === "rejected") {
            rejectedFindingIds.push(item.finding_id);
          } else {
            approvedFindingIds.push(item.finding_id);
          }
        }
        return {
          gate_id: gate.id,
          decision: approvedFindingIds.length ? "approve" as const : "reject" as const,
          reason: "操作人完成中风险批量复核",
          approved_finding_ids: approvedFindingIds,
          rejected_finding_ids: rejectedFindingIds,
          graph_cursor: graphCursor,
          membership_hash: gate.membership_hash!,
        };
      });
      setGateErrors((currentErrors) => ({
        ...currentErrors,
        ...Object.fromEntries(pendingGates.map((gate) => [gate.id, ""])),
      }));
      const result = await agentApi.decideGraphGates(taskId, decisions);
      setGateDecisions((currentDecisions) => ({
        ...currentDecisions,
        ...Object.fromEntries(
          result.decisions.map((decision) => [
            decision.gate_id,
            decision.status,
          ]),
        ),
      }));
    } catch (error) {
      setGateErrors((currentErrors) => ({
        ...currentErrors,
        [pendingGates[0].id]:
          error instanceof Error ? error.message : "中风险复核未完成",
      }));
    } finally {
      await Promise.all([task.refetch(), graph.refetch(), events.refetch()]);
      setGateLoading(undefined);
    }
  }

  return (
    <main className="page-shell task-detail-page agent-task-detail-page apple-page">
      <BackButton fallback="/tasks" label="返回任务列表" />
      <section className="detail-heading"><div><span className="heading-tags"><Tag color={failed ? "error" : terminal ? "success" : blocked ? "error" : terminationRequested ? "warning" : "processing"}>{failed ? "任务失败" : terminationRequested ? "任务已终止" : terminal ? "任务结束" : blocked ? "分析已暂停" : "处理中"}</Tag>{current.task_kind === "rollback" && <Tag color="warning">回滚任务</Tag>}</span><h1>{current.title ?? "Agent 数据同步任务"}</h1><p>后端持久化工作流 · {current.workflow_version}</p></div><div className="detail-total"><span>当前阶段</span><strong>{graph.data?.current_action_zh ?? presentAgentPhase(current.phase)}</strong></div></section>
      {failed && (
        <section className="agent-blocked-notice" aria-live="assertive">
          <div>
            <h2>{current.task_kind === "rollback" ? "回滚任务已停止自动重试" : "任务已停止自动重试"}</h2>
            <p>{current.error?.message ?? "当前阶段未能安全完成。系统已停止自动重试，任务数据未被继续修改。"}</p>
          </div>
        </section>
      )}
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
      {terminal
        && current.task_kind !== "rollback"
        && current.rollback_blocked_reason === "already_rolled_back" && (
        <div className="agent-task-actions">
          <Button
            danger
            disabled
            icon={<RotateCcw size={15} />}
            title={rollbackCycleLockedMessage}
          >
            已经回滚
          </Button>
        </div>
      )}
      <TaskStatusRail
        stages={activePhases.map((phase) => {
          const Icon = phase.icon;
          return {
            id: phase.id,
            label: phase.label,
            icon: <Icon size={14} />,
          };
        })}
        currentIndex={completed}
        blocked={blocked}
        failed={failed}
        terminationRequested={terminationRequested}
      />
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
      {clarificationConfirmedNotice && graph.data?.current_node === "resolve_identity_conflicts" && (
        <Alert
          type="success"
          showIcon
          message="身份冲突选择已确认，Agent 正在继续处理。"
        />
      )}
      {mediumGates.length > 0 && (
        <section
          className="graph-approval-card graph-medium-review-panel"
          aria-label="中风险批量审核"
        >
          <header className="graph-medium-review-heading">
            <div>
              <Tag color="processing">
                {hasMediumOptIn ? "中风险 · 部分项目需主动选择" : "中风险 · 默认全部同意"}
              </Tag>
              <h2>中风险治理建议</h2>
              <p>
                共 {new Set(mediumGates.flatMap((gate) =>
                  (gate.items ?? []).map((item) => item.finding_id),
                )).size} 条记录，
                已归入 {mediumReviewGroups.length} 类操作。普通项目默认同意；清空班级仅在主动勾选后执行。
              </p>
            </div>
          </header>
          <div className="graph-medium-review-groups">
            {mediumReviewGroups.map((group) => {
              const groupStatuses = group.entries.map(
                ({ gate }) => gateDecisions[gate.id] ?? gate.status,
              );
              const reviewCompleted = groupStatuses.every(
                (status) => status !== "pending",
              );
              return (
                <article
                  className={`graph-medium-review-group ${reviewCompleted ? "graph-approval-completed" : "graph-approval-pending"}`}
                  key={group.key}
                >
                  <div className="graph-medium-review-group-heading">
                    <div>
                      <h3>{mediumReviewGroupTitle(group)}</h3>
                      <div className="graph-medium-review-summaries">
                        {group.summaries.map((summary) => (
                          <span key={summary}>{summary}</span>
                        ))}
                      </div>
                    </div>
                    {reviewCompleted ? (
                      <Tag color="success">已完成复核</Tag>
                    ) : (
                      <Tag color="processing">
                        {group.entries.some(({ item }) => isOptInItem(item))
                          ? "包含主动选择项"
                          : "默认同意"}
                      </Tag>
                    )}
                  </div>
                  <details
                    className="graph-approval-details graph-medium-review-details"
                    open={group.entries.length <= 3}
                  >
                    <summary>查看并调整 {group.entries.length} 条具体操作</summary>
                    <ol>
                      {group.entries.map(({ gate, item }) => (
                        <ApprovalItemRow
                          gate={gate}
                          item={item}
                          key={item.finding_id}
                          reviewDecision={reviewDecisionFor(gate, item.finding_id)}
                          reviewCompleted={reviewCompleted}
                          onReview={
                            !reviewCompleted
                              && gate.actionable !== false
                              && gateLoading !== "medium-risk-bulk-review"
                              ? setMediumItemDecision
                              : undefined
                          }
                        />
                      ))}
                    </ol>
                  </details>
                  {[...new Set(group.entries.map(({ gate }) => gate.id))]
                    .map((gateId) => gateErrors[gateId] && (
                      <Alert
                        key={gateId}
                        type="error"
                        showIcon
                        message={gateErrors[gateId]}
                      />
                    ))}
                </article>
              );
            })}
          </div>
          {pendingMediumGates.length > 0 && (
            <div className="graph-approval-actions graph-medium-review-actions">
              <Button
                type="primary"
                loading={gateLoading === "medium-risk-bulk-review"}
                onClick={() => void submitMediumRiskReviews()}
              >
                {mediumSubmitLabel}
              </Button>
            </div>
          )}
        </section>
      )}
      {otherGates.map((gate) => gate.kind === "identity_conflict" ? (() => {
        const conflicts = (gate.conflicts ?? []).filter(
          (conflict) =>
            !confirmedClarificationIds.includes(conflict.clarification_id),
        );
        if (!conflicts.length) {
          return (
          <section className="graph-approval-card graph-clarification-card" key={gate.id}>
            <Alert
              type="error"
              showIcon
              message={
                gate.unavailable_reason_zh
                ?? "冲突明细不完整，不能要求用户盲目判断。"
              }
            />
          </section>
          );
        }
        return conflicts.map((conflict, conflictIndex) => (
          <IdentityConflictClarificationCard
            key={conflict.clarification_id}
            taskId={taskId}
            gate={gate}
            conflict={conflict}
            conflictIndex={conflictIndex}
            conflictCount={conflicts.length}
            graphCursor={graph.data?.graph_cursor ?? gate.cursor ?? 0}
            api={{
              submitClarificationSelection: agentApi.submitClarificationSelection,
              confirmClarification: agentApi.confirmClarification,
            }}
            onOptimisticSubmission={(clarificationId, submission) => {
              queryClient.setQueryData<AgentGraphProgress>(
                ["agent-task-graph", taskId],
                (currentGraph) => updateConflictSubmission(
                  currentGraph,
                  clarificationId,
                  submission,
                ),
              );
            }}
            onRefresh={async () => {
              await Promise.all([task.refetch(), graph.refetch(), events.refetch()]);
            }}
            onConfirmed={(clarificationId) => {
              setConfirmedClarificationIds((currentIds) => [
                ...new Set([...currentIds, clarificationId]),
              ]);
              setClarificationConfirmedNotice(true);
            }}
          />
        ));
      })() : (
        <section
          className={`graph-approval-card graph-approval-${gateDecisions[gate.id] ?? gate.status}`}
          key={gate.id}
          data-risk-approval-id={
            gate.kind === "high_risk_approval" ? gate.id : undefined
          }
          data-risk-approval-status={
            gate.kind === "high_risk_approval"
              ? gateDecisions[gate.id] ?? gate.status
              : undefined
          }
          data-risk-approval-selectable={
            gate.kind === "high_risk_approval"
              ? String(
                (gateDecisions[gate.id] ?? gate.status) === "pending"
                && gate.actionable !== false,
              )
              : undefined
          }
          data-risk-approval-heading={
            gate.kind === "high_risk_approval" ? "" : undefined
          }
          tabIndex={gate.kind === "high_risk_approval" ? -1 : undefined}
        >
          <div className="graph-approval-main">
            {(gateDecisions[gate.id] ?? gate.status) === "approved" ? (
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
            <div className="graph-approval-actions">
              <Button icon={<X size={14} />} loading={gateLoading === gate.id} onClick={() => void decideGate(gate, "reject")}>拒绝</Button>
              <Button type="primary" icon={<Check size={14} />} loading={gateLoading === gate.id} onClick={() => void decideGate(gate, "approve")}>同意</Button>
            </div>
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
      ) : !graph.data && !blocked && <Progress percent={terminal ? 100 : Math.round((completed / activePhases.length) * 100)} showInfo={false} />}
      <Modal
        rootClassName="apple-agent-modal"
        title={
          rollbackPreview?.state === "completed"
            ? "该任务已完成回滚"
            : rollbackPreview?.requires_confirmation
              ? "确认创建独立回滚任务？"
              : "已有回滚任务"
        }
        open={Boolean(rollbackPreview)}
        okText={rollbackPreview?.requires_confirmation ? "确认回滚" : "查看回滚任务"}
        cancelText={rollbackPreview?.requires_confirmation ? "暂不回滚" : "关闭"}
        okButtonProps={{ danger: rollbackPreview?.requires_confirmation }}
        confirmLoading={rollbackPreview?.requires_confirmation && rollbackLoading}
        closable={!rollbackLoading}
        maskClosable={!rollbackLoading}
        onOk={() => {
          if (rollbackPreview?.requires_confirmation) {
            void confirmRollback();
          } else {
            openExistingRollback();
          }
        }}
        onCancel={dismissRollbackPreview}
      >
        {rollbackPreview?.requires_confirmation ? (
          <p>将根据 {rollbackPreview.operation_count} 条已验证变更生成补偿操作。回滚会重新锁定全校数据，并生成独立报告。</p>
        ) : (
          <p>{rollbackPreview?.message_zh}</p>
        )}
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

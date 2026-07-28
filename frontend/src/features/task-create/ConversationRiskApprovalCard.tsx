import { useEffect, useState } from "react";

import type { AgentGraphHumanGate } from "../../api/agent";
import { advanceToNextPendingRiskHeading } from "../agent-approvals/advanceToNextRisk";

type GateDecision = "approved" | "rejected";

const entityFallback = {
  student: "学生记录",
  teacher: "教师记录",
  department: "部门记录",
} as const;

function displayValue(value: string | null | undefined) {
  return value?.trim() || "空值";
}

function personLabel(
  item: NonNullable<AgentGraphHumanGate["items"]>[number],
) {
  const name = item.entity_name?.trim()
    || entityFallback[item.entity_kind]
    || "待治理记录";
  const number = item.entity_number?.trim();
  return number ? `${name}（${number}）` : name;
}

export function ConversationRiskApprovalCard({
  gate,
  onDecide,
}: {
  gate: AgentGraphHumanGate;
  onDecide: (
    gate: AgentGraphHumanGate,
    decision: "approve" | "reject",
  ) => Promise<GateDecision>;
}) {
  const [decision, setDecision] = useState<GateDecision | undefined>(
    gate.status === "approved" || gate.status === "rejected"
      ? gate.status
      : undefined,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const items = gate.items ?? [];
  const canDecide = gate.status === "pending"
    && gate.actionable === true
    && typeof gate.cursor === "number"
    && Boolean(gate.membership_hash)
    && items.length > 0;

  useEffect(() => {
    if (gate.status === "approved" || gate.status === "rejected") {
      setDecision(gate.status);
    }
  }, [gate.status]);

  async function decide(nextDecision: "approve" | "reject") {
    if (!canDecide || loading) return;
    setLoading(true);
    setError(undefined);
    try {
      const completedDecision = await onDecide(gate, nextDecision);
      setDecision(completedDecision);
      advanceToNextPendingRiskHeading(gate.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "审批提交失败，请重试");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section
      className="conversation-risk-approval"
      aria-label="高风险治理操作"
      data-risk-approval-id={gate.id}
      data-risk-approval-status={decision ?? gate.status}
      data-risk-approval-selectable={String(!decision && canDecide)}
    >
      <header className="conversation-risk-header">
        <div>
          <span>高风险操作</span>
          <strong data-risk-approval-heading tabIndex={-1}>
            需要你的确认
          </strong>
        </div>
        {decision && (
          <span className={`conversation-risk-decision ${decision}`}>
            {decision === "approved" ? "已同意" : "已拒绝"}
          </span>
        )}
      </header>

      <div className="conversation-risk-items">
        {items.map((item) => (
          <article className="conversation-risk-item" key={item.finding_id}>
            <div className="conversation-risk-subject">
              <strong>{personLabel(item)}</strong>
              <span>{item.operation_zh}</span>
            </div>
            <div className="conversation-risk-changes">
              {item.changes.map((change) => (
                <div
                  className="conversation-risk-change"
                  key={`${item.finding_id}-${change.field}`}
                >
                  <span>{change.field_zh}</span>
                  <del>{displayValue(change.before)}</del>
                  <span aria-hidden="true">→</span>
                  <ins>{displayValue(change.after)}</ins>
                </div>
              ))}
            </div>
          </article>
        ))}
      </div>

      {!decision && canDecide && (
        <div className="conversation-risk-actions">
          <button
            type="button"
            disabled={loading}
            onClick={() => void decide("approve")}
          >
            同意高风险操作
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={() => void decide("reject")}
          >
            拒绝高风险操作
          </button>
        </div>
      )}
      {!decision && !canDecide && (
        <small className="conversation-risk-unavailable">
          {gate.unavailable_reason_zh || "审批信息尚未完整冻结，请等待任务刷新。"}
        </small>
      )}
      {error && <small className="conversation-risk-error">{error}</small>}
    </section>
  );
}

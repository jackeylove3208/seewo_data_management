import { useEffect, useMemo, useRef, useState } from "react";

import type {
  AgentGraphApprovalItem,
  AgentGraphHumanGate,
} from "../../api/agent";

type GateDecisionStatus = "approved" | "rejected";

interface ReviewEntry {
  gate: AgentGraphHumanGate;
  item: AgentGraphApprovalItem;
}

const entityFallback = {
  student: "学生记录",
  teacher: "教师记录",
  department: "部门记录",
} as const;

function displayValue(value: string | null | undefined) {
  return value?.trim() || "空值";
}

function personLabel(item: AgentGraphApprovalItem) {
  const name = item.entity_name?.trim()
    || entityFallback[item.entity_kind]
    || "待治理记录";
  const number = item.entity_number?.trim();
  return number ? `${name}（${number}）` : name;
}

function collectEntries(gates: AgentGraphHumanGate[]) {
  const seenFindingIds = new Set<string>();
  const entries: ReviewEntry[] = [];
  for (const gate of gates) {
    for (const item of gate.items ?? []) {
      if (seenFindingIds.has(item.finding_id)) continue;
      seenFindingIds.add(item.finding_id);
      entries.push({ gate, item });
    }
  }
  return entries;
}

function isOptIn(item: AgentGraphApprovalItem) {
  return item.selection_mode === "opt_in";
}

function optInLabel(item: AgentGraphApprovalItem, subject: string) {
  return item.changes.length === 1 && item.changes[0]?.field === "class_name"
    ? `将${subject}的班级设置为空`
    : `同意${subject}的整项变更（包括将班级设置为空）`;
}

function initialRejectedFindingIds(gates: AgentGraphHumanGate[]) {
  const rejected = new Set<string>();
  for (const gate of gates) {
    for (const item of gate.items ?? []) {
      const persisted = gate.member_decisions?.[item.finding_id];
      if (persisted === "rejected" || (persisted !== "approved" && isOptIn(item))) {
        rejected.add(item.finding_id);
      }
    }
  }
  return rejected;
}

export function ConversationMediumRiskReviewCard({
  gates,
  onSubmit,
}: {
  gates: AgentGraphHumanGate[];
  onSubmit: (
    gates: AgentGraphHumanGate[],
    rejectedFindingIds: Set<string>,
  ) => Promise<Record<string, GateDecisionStatus>>;
}) {
  const entries = useMemo(() => collectEntries(gates), [gates]);
  const [rejectedFindingIds, setRejectedFindingIds] = useState<Set<string>>(
    () => initialRejectedFindingIds(gates),
  );
  const initializedSelectionModes = useRef(
    new Map(entries.map(({ item }) => [
      item.finding_id,
      item.selection_mode ?? "opt_out",
    ])),
  );
  const editedFindingIds = useRef(new Set<string>());
  const [completionStatuses, setCompletionStatuses] = useState<
    Partial<Record<string, GateDecisionStatus>>
  >({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const pendingGates = gates.filter((gate) =>
    (completionStatuses[gate.id] ?? gate.status) === "pending",
  );
  const pendingGateIds = new Set(pendingGates.map((gate) => gate.id));
  const pendingEntries = entries.filter(({ gate }) => pendingGateIds.has(gate.id));
  const completed = gates.length > 0 && pendingGates.length === 0;
  const canSubmit = pendingGates.length > 0
    && pendingEntries.length > 0
    && pendingGates.every((gate) =>
      gate.actionable === true
      && typeof gate.cursor === "number"
      && Boolean(gate.membership_hash)
      && Boolean(gate.items?.length),
  );
  const rejectedCount = pendingEntries.filter(({ item }) =>
    rejectedFindingIds.has(item.finding_id),
  ).length;
  const approvedCount = pendingEntries.length - rejectedCount;
  const hasPendingOptIn = pendingEntries.some(({ item }) => isOptIn(item));
  const submitLabel = rejectedCount > 0
    ? `按当前选择继续（同意 ${approvedCount}，拒绝 ${rejectedCount}）`
    : hasPendingOptIn
      ? `同意 ${approvedCount} 项并继续`
      : "全部同意并继续";

  useEffect(() => {
    setCompletionStatuses((current) => ({
      ...current,
      ...Object.fromEntries(
        gates
          .filter((gate) => ["approved", "rejected"].includes(gate.status))
          .map((gate) => [gate.id, gate.status as GateDecisionStatus]),
      ),
    }));
    setRejectedFindingIds((current) => {
      const next = new Set(current);
      let changed = false;
      for (const gate of gates) {
        for (const item of gate.items ?? []) {
          const findingId = item.finding_id;
          const decision = gate.member_decisions?.[findingId];
          const selectionMode = item.selection_mode ?? "opt_out";
          const previousMode = initializedSelectionModes.current.get(findingId);
          if (decision === "rejected" && !next.has(findingId)) {
            next.add(findingId);
            changed = true;
          } else if (decision === "approved" && next.delete(findingId)) {
            changed = true;
          } else if (
            decision === undefined
            && previousMode !== selectionMode
            && !editedFindingIds.current.has(findingId)
          ) {
            if (selectionMode === "opt_in" && !next.has(findingId)) {
              next.add(findingId);
              changed = true;
            } else if (selectionMode === "opt_out" && next.delete(findingId)) {
              changed = true;
            }
          }
          initializedSelectionModes.current.set(findingId, selectionMode);
        }
      }
      return changed ? next : current;
    });
  }, [gates]);

  function toggleRejected(findingId: string) {
    editedFindingIds.current.add(findingId);
    setRejectedFindingIds((current) => {
      const next = new Set(current);
      if (next.has(findingId)) {
        next.delete(findingId);
      } else {
        next.add(findingId);
      }
      return next;
    });
  }

  async function submit() {
    if (!canSubmit || loading) return;
    setLoading(true);
    setError(undefined);
    try {
      const pendingFindingIds = new Set(
        pendingEntries.map(({ item }) => item.finding_id),
      );
      const pendingRejections = new Set(
        [...rejectedFindingIds].filter((findingId) =>
          pendingFindingIds.has(findingId),
        ),
      );
      setCompletionStatuses(await onSubmit(pendingGates, pendingRejections));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "中风险复核未完成，请重试");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section
      className="conversation-medium-review"
      aria-label="中风险批量审核"
    >
      <header className="conversation-medium-review-header">
        <div>
          <span>
            {entries.some(({ item }) => isOptIn(item))
              ? "中风险 · 部分项目需主动选择"
              : "中风险 · 默认全部同意"}
          </span>
          <strong>中风险治理建议</strong>
          <small>
            共 {entries.length} 项。普通项目默认同意；清空班级仅在主动勾选后执行。
          </small>
        </div>
        {completed && <span className="conversation-medium-review-completed">已完成复核</span>}
      </header>

      <div className="conversation-medium-review-items">
        {entries.map(({ gate, item }) => {
          const rejected = rejectedFindingIds.has(item.finding_id);
          const optIn = isOptIn(item);
          const itemCompleted = !pendingGateIds.has(gate.id);
          const label = personLabel(item);
          const checked = optIn ? !rejected : rejected;
          return (
            <article className="conversation-medium-review-item" key={item.finding_id}>
              <label>
                <input
                  type="checkbox"
                  aria-label={
                    optIn
                      ? optInLabel(item, label)
                      : `拒绝${label}`
                  }
                  checked={checked}
                  disabled={itemCompleted || loading}
                  onChange={() => toggleRejected(item.finding_id)}
                />
                <span>
                  {optIn
                    ? item.changes.length === 1
                      ? "将班级设置为空"
                      : "同意整项变更（包括清空班级）"
                    : "拒绝此项"}
                </span>
              </label>
              <div className="conversation-risk-subject">
                <strong>{label}</strong>
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
          );
        })}
      </div>

      {!completed && canSubmit && (
        <button
          className="conversation-medium-review-submit"
          type="button"
          disabled={loading}
          onClick={() => void submit()}
        >
          {submitLabel}
        </button>
      )}
      {!completed && !canSubmit && (
        <small className="conversation-risk-unavailable">
          {gates.find((gate) => gate.unavailable_reason_zh)?.unavailable_reason_zh
            || "审核清单尚未完整冻结，请等待任务刷新。"}
        </small>
      )}
      {error && <small className="conversation-risk-error">{error}</small>}
    </section>
  );
}

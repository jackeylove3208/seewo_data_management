import { useEffect, useState } from "react";

import type {
  AgentClarificationConfirmation,
  AgentClarificationInterpretation,
  AgentClarificationSubmission,
  AgentConversationApi,
  AgentGraphHumanGate,
  AgentGraphIdentityConflict,
} from "../api/agent";
import { IdentityConflictEvidence } from "./IdentityConflictEvidence";
import { candidateLabel } from "./identityConflictPresentation";

type ClarificationApi = {
  submitClarificationSelection: NonNullable<
    AgentConversationApi["submitClarificationSelection"]
  >;
  confirmClarification: NonNullable<AgentConversationApi["confirmClarification"]>;
};

export interface IdentityConflictClarificationCardProps {
  taskId: string;
  gate: AgentGraphHumanGate;
  conflict: AgentGraphIdentityConflict;
  conflictIndex: number;
  conflictCount: number;
  graphCursor: number;
  api: ClarificationApi;
  onRefresh: () => void | Promise<void>;
  onOptimisticSubmission?: (
    clarificationId: string,
    submission: AgentClarificationSubmission | null,
  ) => void;
  onConfirmed?: (clarificationId: string) => void;
}

type SelectionValue = `candidate:${string}` | "target_extra" | "";

interface StoredSubmission {
  submission: AgentClarificationSubmission;
  saving: boolean;
}

function storageKey(taskId: string, clarificationId: string) {
  return `identity-clarification:${taskId}:${clarificationId}`;
}

function readStoredSubmission(
  taskId: string,
  clarificationId: string,
): StoredSubmission | undefined {
  if (typeof sessionStorage === "undefined") return undefined;
  const value = sessionStorage.getItem(storageKey(taskId, clarificationId));
  if (!value) return undefined;
  try {
    const parsed = JSON.parse(value) as StoredSubmission;
    if (parsed.submission?.source !== "structured_selection") return undefined;
    return parsed;
  } catch {
    sessionStorage.removeItem(storageKey(taskId, clarificationId));
    return undefined;
  }
}

function writeStoredSubmission(
  taskId: string,
  clarificationId: string,
  value: StoredSubmission | undefined,
) {
  if (typeof sessionStorage === "undefined") return;
  const key = storageKey(taskId, clarificationId);
  if (value) sessionStorage.setItem(key, JSON.stringify(value));
  else sessionStorage.removeItem(key);
}

function requestKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `identity-selection-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function choiceLabel(
  conflict: AgentGraphIdentityConflict,
  submission: AgentClarificationSubmission,
) {
  if (submission.decision === "treat_as_extra") return "按希沃多余处理";
  const index = conflict.candidates.findIndex(
    (candidate) => candidate.candidate_id === submission.selected_candidate_id,
  );
  return index >= 0
    ? `第三方候选 ${candidateLabel(index)}`
    : "已选择的第三方候选";
}

function submissionFromResponse(
  response: AgentClarificationInterpretation,
  note: string | null,
): AgentClarificationSubmission {
  return {
    decision: response.decision === "treat_as_extra"
      ? "treat_as_extra"
      : "select_candidate",
    selected_candidate_id: response.selected_candidate_id,
    note,
    interpretation_zh:
      response.interpretation_zh ?? "选择已保存，确认后继续。",
    submitted_at: new Date().toISOString(),
    source: "structured_selection",
  };
}

export function IdentityConflictClarificationCard({
  taskId,
  gate,
  conflict,
  conflictIndex,
  conflictCount,
  graphCursor,
  api,
  onRefresh,
  onOptimisticSubmission,
  onConfirmed,
}: IdentityConflictClarificationCardProps) {
  const revisionPending = (
    conflict.status === "pending"
    && !conflict.operator_submission
    && Boolean(conflict.interpretation_zh)
  );
  const restoredSubmission = conflict.operator_submission
    ? { submission: conflict.operator_submission, saving: false }
    : readStoredSubmission(taskId, conflict.clarification_id);
  const [selection, setSelection] = useState<SelectionValue>("");
  const [note, setNote] = useState("");
  const [submission, setSubmission] = useState<
    AgentClarificationSubmission | undefined
  >(restoredSubmission?.submission);
  const [editing, setEditing] = useState(!restoredSubmission && !revisionPending);
  const [replacing, setReplacing] = useState(false);
  const [saving, setSaving] = useState(restoredSubmission?.saving ?? false);
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState(conflict.status === "confirmed");
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!conflict.operator_submission) return;
    writeStoredSubmission(taskId, conflict.clarification_id, undefined);
    setSubmission(conflict.operator_submission);
    setSaving(false);
    if (!replacing) setEditing(false);
  }, [
    conflict.clarification_id,
    conflict.operator_submission,
    replacing,
    taskId,
  ]);

  if (confirmed || conflict.status === "confirmed") {
    return (
      <section className="identity-clarification-card is-confirmed" aria-live="polite">
        <strong>身份冲突选择已确认，Agent 正在继续处理。</strong>
      </section>
    );
  }

  async function submitSelection() {
    if (!selection || saving) return;
    const previousSubmission = submission;
    const selectedCandidateId = selection.startsWith("candidate:")
      ? selection.slice("candidate:".length)
      : null;
    const decision = selection === "target_extra"
      ? "treat_as_extra" as const
      : "select_candidate" as const;
    const normalizedNote = note.trim() || null;
    const candidateIndex = conflict.candidates.findIndex(
      (candidate) => candidate.candidate_id === selectedCandidateId,
    );
    const interpretationZh = decision === "treat_as_extra"
      ? "你选择了按希沃多余处理，确认后继续。"
      : `你选择了第三方候选 ${candidateLabel(candidateIndex)}，确认后继续。`;
    const optimisticSubmission: AgentClarificationSubmission = {
      decision,
      selected_candidate_id: selectedCandidateId,
      note: normalizedNote,
      interpretation_zh: interpretationZh,
      submitted_at: new Date().toISOString(),
      source: "structured_selection",
    };
    setError(undefined);
    setSubmission(optimisticSubmission);
    setEditing(false);
    setReplacing(false);
    setSaving(true);
    writeStoredSubmission(taskId, conflict.clarification_id, {
      submission: optimisticSubmission,
      saving: true,
    });
    onOptimisticSubmission?.(conflict.clarification_id, optimisticSubmission);
    try {
      const response = await api.submitClarificationSelection(
        taskId,
        conflict.clarification_id,
        {
          decision,
          selected_candidate_id: selectedCandidateId,
          note: normalizedNote,
          graph_cursor: graphCursor,
          idempotency_key: requestKey(),
        },
      );
      const persistedSubmission = submissionFromResponse(response, normalizedNote);
      setSubmission(persistedSubmission);
      setSaving(false);
      writeStoredSubmission(taskId, conflict.clarification_id, {
        submission: persistedSubmission,
        saving: false,
      });
      onOptimisticSubmission?.(conflict.clarification_id, persistedSubmission);
      void onRefresh();
    } catch (submitError) {
      setSubmission(previousSubmission);
      setSaving(false);
      setEditing(true);
      setReplacing(Boolean(previousSubmission));
      onOptimisticSubmission?.(
        conflict.clarification_id,
        previousSubmission ?? null,
      );
      writeStoredSubmission(
        taskId,
        conflict.clarification_id,
        previousSubmission
          ? { submission: previousSubmission, saving: false }
          : undefined,
      );
      setError(
        submitError instanceof Error ? submitError.message : "身份冲突选择保存失败",
      );
      void onRefresh();
    }
  }

  async function confirmSelection() {
    if (!submission || confirming) return;
    setError(undefined);
    setConfirming(true);
    try {
      const response: AgentClarificationConfirmation =
        await api.confirmClarification(taskId, conflict.clarification_id);
      if (response.status !== "confirmed") {
        throw new Error("身份冲突选择尚未确认");
      }
      setConfirmed(true);
      writeStoredSubmission(taskId, conflict.clarification_id, undefined);
      onConfirmed?.(conflict.clarification_id);
      void onRefresh();
    } catch (confirmError) {
      setError(
        confirmError instanceof Error
          ? confirmError.message
          : "身份冲突选择确认失败",
      );
    } finally {
      setConfirming(false);
    }
  }

  function openReplacement() {
    setSelection("");
    setNote("");
    setError(undefined);
    setReplacing(true);
    setEditing(true);
  }

  return (
    <section
      className="graph-approval-card graph-clarification-card identity-clarification-card"
      aria-label="身份冲突处理"
    >
      <header className="identity-clarification-heading">
        <span>需要判断</span>
        <h2>需要人工判断身份冲突</h2>
        <p>请核对希沃记录与冻结的第三方候选。选择保存后还需再次确认，任务才会继续。</p>
      </header>
      <IdentityConflictEvidence
        conflict={conflict}
        index={conflictIndex}
        total={conflictCount}
      />

      {revisionPending && !submission ? (
        <div className="identity-clarification-submission is-revision">
          <strong>上次说明需要补充</strong>
          <p>{conflict.interpretation_zh}</p>
          {!editing ? (
            <button type="button" onClick={() => setEditing(true)}>
              补充说明
            </button>
          ) : null}
        </div>
      ) : null}

      {submission ? (
        <div className="identity-clarification-submission" aria-live="polite">
          <strong>已选择：{choiceLabel(conflict, submission)}</strong>
          <p>{submission.interpretation_zh}</p>
          {submission.note ? (
            <p className="identity-clarification-note">
              <span>补充说明</span>
              {submission.note}
            </p>
          ) : null}
          <span className={`identity-clarification-state${saving ? " is-saving" : ""}`}>
            {saving ? "正在保存" : "等待确认"}
          </span>
          {!saving && !replacing ? (
            <div className="identity-clarification-actions">
              <button type="button" onClick={openReplacement}>
                重新选择
              </button>
              <button
                type="button"
                className="primary-button"
                disabled={confirming || gate.actionable === false}
                onClick={() => void confirmSelection()}
              >
                {confirming ? "正在确认" : "确认选择并继续"}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {editing ? (
        <div className="identity-clarification-form">
          <fieldset>
            <legend>{replacing ? "重新选择处理方案" : "选择处理方案"}</legend>
            {conflict.candidates.map((candidate, index) => (
              <label key={candidate.candidate_id ?? index}>
                <input
                  type="radio"
                  name={`identity-selection-${conflict.clarification_id}`}
                  value={`candidate:${candidate.candidate_id ?? ""}`}
                  checked={selection === `candidate:${candidate.candidate_id ?? ""}`}
                  disabled={!candidate.candidate_id || saving}
                  onChange={(event) => setSelection(event.target.value as SelectionValue)}
                />
                采用第三方候选 {candidateLabel(index)}
              </label>
            ))}
            {conflict.allowed_outcomes.includes("target_extra") ? (
              <label>
                <input
                  type="radio"
                  name={`identity-selection-${conflict.clarification_id}`}
                  value="target_extra"
                  checked={selection === "target_extra"}
                  disabled={saving}
                  onChange={(event) => setSelection(event.target.value as SelectionValue)}
                />
                按希沃多余处理
              </label>
            ) : null}
          </fieldset>
          <label htmlFor={`identity-note-${conflict.clarification_id}`}>
            补充说明（可选）
          </label>
          <textarea
            id={`identity-note-${conflict.clarification_id}`}
            value={note}
            maxLength={500}
            rows={3}
            disabled={saving}
            onChange={(event) => setNote(event.target.value)}
          />
          <div className="identity-clarification-actions">
            {replacing ? (
              <button
                type="button"
                onClick={() => {
                  setReplacing(false);
                  setEditing(false);
                  setSelection("");
                  setNote("");
                  setError(undefined);
                }}
              >
                取消重新选择
              </button>
            ) : null}
            <button
              type="button"
              className="primary-button"
              disabled={!selection || saving || gate.actionable === false}
              onClick={() => void submitSelection()}
            >
              提交选择
            </button>
          </div>
        </div>
      ) : null}

      {error ? <p className="identity-clarification-error" role="alert">{error}</p> : null}
      {gate.actionable === false && gate.unavailable_reason_zh ? (
        <p className="identity-clarification-error">{gate.unavailable_reason_zh}</p>
      ) : null}
    </section>
  );
}

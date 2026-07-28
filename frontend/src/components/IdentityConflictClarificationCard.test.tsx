import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AgentClarificationConfirmation,
  AgentClarificationInterpretation,
  AgentGraphHumanGate,
  AgentGraphIdentityConflict,
} from "../api/agent";
import { IdentityConflictClarificationCard } from "./IdentityConflictClarificationCard";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const conflict: AgentGraphIdentityConflict = {
  clarification_id: "clarification-1",
  status: "pending",
  summary_zh: "唯一身份字段命中了多个第三方权威候选，Agent 无法安全选择。",
  subject: {
    entity_kind: "student",
    category: "学生",
    name: "测试学生",
    number: "S-009",
    class_name: "一年级一班",
    phone_masked: "***0009",
    email_masked: "s***@example.test",
  },
  candidates: [
    {
      candidate_id: "candidate-a",
      entity_kind: "student",
      category: "学生",
      name: "测试学生",
      number: "S-001",
      class_name: "一年级一班",
      phone_masked: "***0001",
      email_masked: "s***@example.test",
    },
    {
      candidate_id: "candidate-b",
      entity_kind: "student",
      category: "学生",
      name: "测试学生二号",
      number: "S-002",
      class_name: "一年级二班",
      phone_masked: "***0002",
      email_masked: "s***@example.test",
    },
  ],
  allowed_outcomes: ["use_candidate", "target_extra"],
  interpretation_zh: null,
  operator_submission: null,
};

const gate: AgentGraphHumanGate = {
  id: "gate-1",
  kind: "identity_conflict",
  status: "pending",
  item_count: 1,
  cursor: 5,
  actionable: true,
  conflicts: [conflict],
};

function renderCard({
  selectedConflict = conflict,
  submit = vi.fn().mockResolvedValue({
    decision_id: "clarification-1",
    status: "interpreted",
    task_id: "task-1",
    decision: "select_candidate",
    selected_candidate_id: "candidate-a",
    interpretation_zh: "你选择了第三方候选 A，确认后继续。",
    requires_second_confirmation: true,
  } satisfies AgentClarificationInterpretation),
  confirm = vi.fn().mockResolvedValue({
    status: "confirmed",
  } satisfies AgentClarificationConfirmation),
} = {}) {
  const onRefresh = vi.fn();
  const onOptimisticSubmission = vi.fn();
  const onConfirmed = vi.fn();
  const rendered = render(
    <IdentityConflictClarificationCard
      taskId="task-1"
      gate={gate}
      conflict={selectedConflict}
      conflictIndex={0}
      conflictCount={1}
      graphCursor={6}
      api={{
        submitClarificationSelection: submit,
        confirmClarification: confirm,
      }}
      onRefresh={onRefresh}
      onOptimisticSubmission={onOptimisticSubmission}
      onConfirmed={onConfirmed}
    />,
  );
  return {
    ...rendered,
    submit,
    confirm,
    onRefresh,
    onOptimisticSubmission,
    onConfirmed,
  };
}

describe("IdentityConflictClarificationCard", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("turns an explicit choice read only immediately while saving", async () => {
    const pending = deferred<AgentClarificationInterpretation>();
    const submit = vi.fn().mockReturnValue(pending.promise);
    const user = userEvent.setup();
    const { onOptimisticSubmission } = renderCard({ submit });

    await user.click(screen.getByRole("radio", { name: "采用第三方候选 A" }));
    await user.type(screen.getByLabelText("补充说明（可选）"), "采用候选 A");
    await user.click(screen.getByRole("button", { name: "提交选择" }));

    expect(screen.queryByRole("button", { name: "提交选择" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("补充说明（可选）")).not.toBeInTheDocument();
    expect(screen.getByText("已选择：第三方候选 A")).toBeInTheDocument();
    expect(screen.getByText("采用候选 A")).toBeInTheDocument();
    expect(screen.getByText("正在保存")).toBeInTheDocument();
    expect(onOptimisticSubmission).toHaveBeenCalledWith(
      "clarification-1",
      expect.objectContaining({
        decision: "select_candidate",
        selected_candidate_id: "candidate-a",
        note: "采用候选 A",
      }),
    );

    pending.resolve({
      decision_id: "clarification-1",
      status: "interpreted",
      task_id: "task-1",
      decision: "select_candidate",
      selected_candidate_id: "candidate-a",
      interpretation_zh: "你选择了第三方候选 A，确认后继续。",
      requires_second_confirmation: true,
    });

    expect(await screen.findByText("等待确认")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新选择" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "确认选择并继续" }),
    ).toBeInTheDocument();
  });

  it("keeps the previous submission visible while a blank replacement form is open", async () => {
    const user = userEvent.setup();
    renderCard({
      selectedConflict: {
        ...conflict,
        status: "interpreted",
        operator_submission: {
          decision: "select_candidate",
          selected_candidate_id: "candidate-a",
          note: "首次选择",
          interpretation_zh: "你选择了第三方候选 A，确认后继续。",
          submitted_at: "2026-07-28T10:00:00Z",
          source: "structured_selection",
        },
      },
    });

    expect(screen.getByText("首次选择")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新选择" }));

    expect(screen.getByText("首次选择")).toBeInTheDocument();
    expect(screen.getByLabelText("补充说明（可选）")).toHaveValue("");
    expect(screen.getByRole("radio", { name: "采用第三方候选 A" })).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "取消重新选择" }));
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.getByText("首次选择")).toBeInTheDocument();
  });

  it("restores an in-flight read-only choice after route navigation", async () => {
    const pending = deferred<AgentClarificationInterpretation>();
    const submit = vi.fn().mockReturnValue(pending.promise);
    const user = userEvent.setup();
    const first = renderCard({ submit });

    await user.click(screen.getByRole("radio", { name: "采用第三方候选 B" }));
    await user.click(screen.getByRole("button", { name: "提交选择" }));
    expect(screen.getByText("正在保存")).toBeInTheDocument();
    first.unmount();

    renderCard({ submit });

    expect(screen.getByText("已选择：第三方候选 B")).toBeInTheDocument();
    expect(screen.getByText("正在保存")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提交选择" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it("restores the form after a save failure and confirms through the existing boundary", async () => {
    const submit = vi.fn().mockRejectedValue(new Error("候选已刷新"));
    const confirm = vi.fn().mockResolvedValue({ status: "confirmed" });
    const user = userEvent.setup();
    const rendered = renderCard({ submit, confirm });

    await user.click(screen.getByRole("radio", { name: "按希沃多余处理" }));
    await user.type(screen.getByLabelText("补充说明（可选）"), "候选都不对应");
    await user.click(screen.getByRole("button", { name: "提交选择" }));

    expect(await screen.findByText("候选已刷新")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "按希沃多余处理" })).toBeChecked();
    expect(screen.getByLabelText("补充说明（可选）")).toHaveValue("候选都不对应");

    const persisted = {
      ...conflict,
      status: "interpreted",
      operator_submission: {
        decision: "treat_as_extra" as const,
        selected_candidate_id: null,
        note: "候选都不对应",
        interpretation_zh: "你选择了按希沃多余处理，确认后继续。",
        submitted_at: "2026-07-28T10:00:00Z",
        source: "structured_selection" as const,
      },
    };
    screen.getByRole("button", { name: "提交选择" });
    rendered.onRefresh.mockClear();

    // Persisted submissions start directly in the confirmation state.
    rendered.unmount();
    const confirmed = renderCard({ selectedConflict: persisted, confirm });
    await user.click(screen.getByRole("button", { name: "确认选择并继续" }));

    await waitFor(() => {
      expect(confirm).toHaveBeenCalledWith("task-1", "clarification-1");
    });
    expect(confirmed.onConfirmed).toHaveBeenCalledWith("clarification-1");
    expect(
      screen.getByText("身份冲突选择已确认，Agent 正在继续处理。"),
    ).toBeInTheDocument();
  });
});

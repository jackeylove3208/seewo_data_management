import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { MatchingQualityResult, RematchingJobProgress } from "../../api/reconciliation";
import { MatchingRecoveryPanel } from "./MatchingRecoveryPanel";

const running: RematchingJobProgress = {
  job_id: "rematch-1",
  task_id: "task-1",
  status: "running",
  initial_unresolved: 12,
  indexed: 10,
  processed: 4,
  ai_recovered: 2,
  no_match: 1,
  manual_review: 1,
  conflict: 0,
  failed: 0,
  updated_at: "2026-07-20T10:00:00Z",
};

const passed: MatchingQualityResult = {
  task_id: "task-1",
  policy_version: "matching-quality-v1",
  mapping_versions: ["mapping-v2"],
  counts: {
    student: {
      total: 12,
      accepted: 10,
      deterministic: 2,
      ai_recovered: 8,
      manual_review: 1,
      conflict: 0,
      unmatched: 1,
      unconsumed_target: 1,
      predicted_missing: 1,
      predicted_redundant: 1,
    },
  },
  passed: true,
  failures: [],
};

describe("MatchingRecoveryPanel", () => {
  it("shows the no-rematch fast path without claiming an AI call", () => {
    render(<MatchingRecoveryPanel progress={null} quality={passed} />);

    expect(screen.getByText("无需 AI 二次匹配")).toBeInTheDocument();
    expect(screen.getByText("首次匹配已覆盖全部实体，已直接进入质量评估。")).toBeInTheDocument();
  });

  it("shows stable running stages and Chinese counters", () => {
    render(<MatchingRecoveryPanel progress={running} quality={null} onCancel={vi.fn()} />);

    expect(screen.getByText("实体匹配恢复中")).toBeInTheDocument();
    expect(screen.getByText("首次匹配")).toBeInTheDocument();
    expect(screen.getByText("向量索引")).toBeInTheDocument();
    expect(screen.getByText("AI 恢复")).toBeInTheDocument();
    expect(screen.getByText("全局分配")).toBeInTheDocument();
    expect(screen.getByText("质量评估")).toBeInTheDocument();
    expect(screen.getByText("首次未匹配")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("AI 已恢复")).toBeInTheDocument();
    expect(screen.getByText("剩余人工")).toBeInTheDocument();
    expect(screen.getByText("冲突")).toBeInTheDocument();
    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("最近更新 18:00:00")).toBeInTheDocument();
  });

  it("reports successful recovery and zero-candidate outcomes separately", () => {
    render(<MatchingRecoveryPanel progress={{ ...running, status: "completed", indexed: 12, processed: 12, ai_recovered: 8, no_match: 2, manual_review: 1, conflict: 1 }} quality={passed} />);

    expect(screen.getByText("实体匹配已完成")).toBeInTheDocument();
    expect(screen.getByText("未找到候选")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("质量门禁已通过")).toBeInTheDocument();
  });

  it("offers retry and manual mapping for an actionable gate failure", async () => {
    const user = userEvent.setup();
    const retry = vi.fn();
    const manual = vi.fn();
    const blocked: MatchingQualityResult = {
      ...passed,
      passed: false,
      failures: [{
        code: "matching_quality_gate_failed",
        affected_entity_types: ["student", "class"],
        reason: "学生剩余未解析比例过高，且班级上下文仍未确认。",
        observed_value: 0.42,
        threshold: 0.2,
        recovery_actions: ["重试 AI 二次匹配", "人工确认班级映射"],
      }],
    };
    render(<MatchingRecoveryPanel progress={{ ...running, status: "completed", indexed: 12, processed: 12, ai_recovered: 3, no_match: 5, manual_review: 3, conflict: 1 }} quality={blocked} onRetry={retry} onManualMapping={manual} />);

    expect(screen.getByText("匹配质量未通过，差异检测已暂停")).toBeInTheDocument();
    expect(screen.getByText("学生、班级")).toBeInTheDocument();
    expect(screen.getByText("实际值 42.0%")).toBeInTheDocument();
    expect(screen.getByText("阈值 20.0%")).toBeInTheDocument();
    expect(screen.getByText("当前仅更新匹配判断，不会修改三方系统、希沃或 CSV 数据。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试匹配" }));
    await user.click(screen.getByRole("button", { name: "人工确认映射" }));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(manual).toHaveBeenCalledTimes(1);
  });

  it("uses a localized recoverable error", async () => {
    const user = userEvent.setup();
    const reload = vi.fn();
    render(<MatchingRecoveryPanel progress={running} quality={null} loadFailed onReload={reload} />);

    expect(screen.getByText("匹配进度读取失败")).toBeInTheDocument();
    expect(screen.queryByText(/network|internal/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

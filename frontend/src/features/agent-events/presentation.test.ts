import { describe, expect, it } from "vitest";

import type { AgentTaskEvent } from "../../api/agent";
import { presentAgentEvent } from "./presentation";

function event(
  type: string,
  payload: Record<string, unknown> = {},
  phase?: AgentTaskEvent["phase"],
): AgentTaskEvent {
  return {
    id: `${type}-1`,
    cursor: "1",
    type,
    phase,
    payload,
    created_at: "2026-07-24T03:10:00Z",
  };
}

describe("Agent event presentation", () => {
  it.each([
    ["run.created", "任务已创建"],
    ["school_lock.acquired", "已锁定学校数据"],
    ["agent_ingestion_persisted", "数据接入完成"],
    ["agent_identity_work_persisted", "身份索引已建立"],
    ["agent_analysis_completed", "Agent 分析完成"],
    ["approval_required", "等待高风险操作审批"],
    ["agent_plan_compiled", "治理方案已生成"],
    ["report_ready", "任务报告已生成"],
    ["run.terminated", "任务已终止"],
  ])("presents %s as Chinese business copy", (type, expected) => {
    expect(presentAgentEvent(event(type)).title).toBe(expected);
  });

  it("shows bounded model-attempt progress without exposing technical identifiers", () => {
    const presented = presentAgentEvent(event("model_attempt_started", {
      attempt: 2,
      attempt_count: 4,
      entity_kind: "student",
    }));

    expect(presented.title).toBe("Agent 正在分析学生数据");
    expect(presented.description).toContain("第 2/4 次");
    expect(`${presented.title}${presented.description}`).not.toContain(
      "model_attempt_started",
    );
  });

  it("explains a timeout and the final blocked state in Chinese", () => {
    const failed = presentAgentEvent(event("model_attempt_failed", {
      attempt: 4,
      attempt_count: 4,
      failure_category: "model_timeout",
    }));
    const exhausted = presentAgentEvent(event("model_retry_exhausted", {
      attempt_count: 4,
    }));

    expect(failed.title).toBe("模型响应超时");
    expect(failed.tone).toBe("warning");
    expect(exhausted.title).toBe("模型分析已暂停");
    expect(exhausted.description).toContain("4 次");
    expect(exhausted.tone).toBe("danger");
  });

  it("uses a safe Chinese fallback for unknown audit events", () => {
    const presented = presentAgentEvent(event("internal.future_event"));

    expect(presented.title).toBe("任务状态已更新");
    expect(presented.description).not.toContain("internal.future_event");
  });
});

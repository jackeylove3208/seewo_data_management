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

  it("explains the actual failed contract instead of blaming the model service", () => {
    const rejectedArgument = presentAgentEvent(event("run.blocked_model_error", {
      attempt_count: 4,
      failed_node: "analyze_actionable_batches",
      failure_categories: ["tool_argument_rejected"],
    }));
    const authorizationFailure = presentAgentEvent(event("run.blocked_model_error", {
      attempt_count: 1,
      failed_node: "analyze_actionable_batches",
      failure_categories: ["tool_authorization_failure"],
    }));
    const evidenceFailure = presentAgentEvent(event("run.blocked_model_error", {
      attempt_count: 0,
      failed_node: "analyze_actionable_batches",
      failure_categories: ["evidence_manifest_missing"],
    }));
    const inputContractFailure = presentAgentEvent(event("run.blocked_model_error", {
      attempt_count: 0,
      failed_node: "analyze_actionable_batches",
      failure_categories: ["model_input_contract_failure"],
    }));

    expect(rejectedArgument.description).toContain("工具参数");
    expect(rejectedArgument.description).toContain("证据清单");
    expect(rejectedArgument.description).not.toContain("检查模型服务");
    expect(authorizationFailure.description).toContain("授权状态");
    expect(authorizationFailure.description).toContain("1 次");
    expect(authorizationFailure.description).not.toContain("检查模型服务");
    expect(evidenceFailure.description).toContain("证据清单");
    expect(evidenceFailure.description).toContain("0 次");
    expect(evidenceFailure.description).not.toContain("检查模型服务");
    expect(inputContractFailure.description).toContain("输入合同");
    expect(inputContractFailure.description).not.toContain("检查模型服务");
  });

  it("uses a safe Chinese fallback for unknown audit events", () => {
    const presented = presentAgentEvent(event("internal.future_event"));

    expect(presented.title).toBe("任务状态已更新");
    expect(presented.description).not.toContain("internal.future_event");
  });

  it("presents graph transitions as specific Chinese business progress", () => {
    const normalized = presentAgentEvent(
      event("graph.transitioned", {
        action_id: "normalize_next_batch",
        node: "normalize_input_batches",
      }),
    );
    const approval = presentAgentEvent(
      event("graph.transitioned", {
        action_id: "aggregate_risk",
        node: "wait_high_risk_approvals",
      }),
    );

    expect(normalized.title).toBe("数据规范化批次已完成");
    expect(approval.title).toBe("风险与审批已汇总");
    expect(normalized.description).not.toContain("内部审计");
  });
});

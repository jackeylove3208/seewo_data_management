import type { AgentPhase, AgentTaskEvent } from "../../api/agent";

export type AgentEventTone = "info" | "success" | "warning" | "danger";

export interface PresentedAgentEvent {
  title: string;
  description: string;
  time: string;
  tone: AgentEventTone;
}

const phaseLabels: Partial<Record<AgentPhase, string>> = {
  intent_confirmed: "同步需求已确认",
  acquire_school_lock: "正在锁定学校数据",
  ingest_and_normalize: "数据接入与规范化",
  build_identity_work: "正在建立身份索引",
  analyze_batches: "Agent 分析与治理方案",
  clarify_identity_conflicts: "正在等待身份冲突说明",
  aggregate_risk_and_approvals: "正在汇总风险与审批",
  compile_execution_plan: "正在编译治理方案",
  execute_and_verify: "正在执行并验证治理操作",
  generate_report: "正在生成任务报告",
  plan_restore: "正在规划回滚任务",
  clarify_restore_conflicts: "正在确认回滚冲突",
  approve_restore: "正在等待回滚确认",
  execute_restore: "正在执行并验证回滚",
  report_restore: "正在生成回滚报告",
  terminal: "任务流程已结束",
};

const entityLabels: Record<string, string> = {
  student: "学生",
  teacher: "老师",
  department: "部门",
};

const staticEvents: Record<string, Omit<PresentedAgentEvent, "time">> = {
  "run.created": {
    title: "任务已创建",
    description: "任务已进入后端持久化处理队列。",
    tone: "info",
  },
  "school_lock.acquired": {
    title: "已锁定学校数据",
    description: "当前任务已获得全校排他锁，避免并发治理冲突。",
    tone: "success",
  },
  agent_ingestion_persisted: {
    title: "数据接入完成",
    description: "输入数据已经规范化并持久保存。",
    tone: "success",
  },
  agent_identity_work_persisted: {
    title: "身份索引已建立",
    description: "编号、电话和邮箱身份线索已经整理完成。",
    tone: "success",
  },
  agent_analysis_completed: {
    title: "Agent 分析完成",
    description: "需要处理的数据已经生成分析与治理方案。",
    tone: "success",
  },
  approval_required: {
    title: "等待高风险操作审批",
    description: "同类高风险操作已合并，确认后才会进入治理执行。",
    tone: "warning",
  },
  agent_approvals_aggregated: {
    title: "风险与审批已汇总",
    description: "Agent 已按风险策略整理治理操作。",
    tone: "success",
  },
  agent_plan_compiled: {
    title: "治理方案已生成",
    description: "服务端已将 Agent 方案编译为受控操作。",
    tone: "success",
  },
  report_ready: {
    title: "任务报告已生成",
    description: "任务事实、治理结果和回滚依据已写入报告。",
    tone: "success",
  },
  "report.completed": {
    title: "任务报告已生成",
    description: "任务事实、治理结果和回滚依据已写入报告。",
    tone: "success",
  },
  "run.terminated": {
    title: "任务已终止",
    description: "后端已停止后续处理并保存终止报告。",
    tone: "warning",
  },
  abnormal_input_report_ready: {
    title: "输入数据不符合规范",
    description: "任务已跳过治理执行并生成异常报告。",
    tone: "danger",
  },
  clarification_required: {
    title: "需要补充身份冲突说明",
    description: "现有证据不足以让 Agent 安全决定，请提供人工意见。",
    tone: "warning",
  },
  clarification_decision_ready: {
    title: "身份冲突解释待确认",
    description: "Agent 已将人工说明解释为受控决策，请进行二次确认。",
    tone: "warning",
  },
  "termination.report.persisted": {
    title: "终止报告已保存",
    description: "已完成操作保持不变，后续处理已经停止。",
    tone: "warning",
  },
};

export function presentAgentEvent(event: AgentTaskEvent): PresentedAgentEvent {
  const time = formatAgentEventTime(event.created_at);
  const staticEvent = staticEvents[event.type];
  if (staticEvent) return { ...staticEvent, time };

  if (event.type === "phase.started" || event.type === "phase.transitioned") {
    const phase = event.phase ?? payloadPhase(event);
    return {
      title: phase ? phaseLabels[phase] ?? "任务阶段已更新" : "任务阶段已更新",
      description:
        event.type === "phase.started"
          ? "后端已开始处理当前阶段。"
          : "上一阶段已完成，任务已安全进入下一阶段。",
      tone: "info",
      time,
    };
  }

  if (event.type === "model_attempt_started") {
    const entity = entityLabels[payloadString(event, "entity_kind")] ?? "组织";
    return {
      title: `Agent 正在分析${entity}数据`,
      description: attemptDescription(event, "正在等待模型返回结构化分析。"),
      tone: "info",
      time,
    };
  }

  if (event.type === "model_attempt_succeeded") {
    return {
      title: "本批 Agent 分析完成",
      description: attemptDescription(event, "模型输出已经通过服务端结构校验。"),
      tone: "success",
      time,
    };
  }

  if (event.type === "model_attempt_failed") {
    const category = payloadString(event, "failure_category");
    const title = {
      model_timeout: "模型响应超时",
      model_transport_failure: "模型连接暂时失败",
      model_provider_failure: "模型服务拒绝了请求",
      model_output_invalid: "模型输出未通过校验",
    }[category] ?? "本次模型分析失败";
    return {
      title,
      description: attemptDescription(event, "系统将按受控重试策略继续处理。"),
      tone: "warning",
      time,
    };
  }

  if (event.type === "model_retry_exhausted" || event.type === "run.blocked_model_error") {
    const attempts = payloadNumber(event, "attempt_count") ?? 4;
    return {
      title: "模型分析已暂停",
      description: `连续 ${attempts} 次模型分析均未成功。任务数据和学校锁仍被安全保留，请终止任务后检查模型服务。`,
      tone: "danger",
      time,
    };
  }

  return {
    title: "任务状态已更新",
    description: "系统已记录一条内部审计事件。",
    tone: "info",
    time,
  };
}

export function presentAgentPhase(phase: AgentPhase): string {
  return phaseLabels[phase] ?? "任务处理中";
}

function payloadString(event: AgentTaskEvent, key: string): string {
  const value = event.payload[key];
  return typeof value === "string" ? value : "";
}

function payloadNumber(event: AgentTaskEvent, key: string): number | undefined {
  const value = event.payload[key];
  return typeof value === "number" ? value : undefined;
}

function payloadPhase(event: AgentTaskEvent): AgentPhase | undefined {
  const value = payloadString(event, "phase");
  return value in phaseLabels ? value as AgentPhase : undefined;
}

function attemptDescription(event: AgentTaskEvent, suffix: string): string {
  const attempt = payloadNumber(event, "attempt");
  const attemptCount = payloadNumber(event, "attempt_count") ?? 4;
  return attempt ? `第 ${attempt}/${attemptCount} 次模型调用。${suffix}` : suffix;
}

function formatAgentEventTime(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

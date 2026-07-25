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

const graphActionEvents: Record<string, Omit<PresentedAgentEvent, "time">> = {
  inspect_authority: {
    title: "第三方数据结构检查完成",
    description: "Agent 已识别第三方权威数据的结构和字段。",
    tone: "success",
  },
  inspect_target: {
    title: "希沃数据结构检查完成",
    description: "Agent 已识别希沃目标数据的结构和字段。",
    tone: "success",
  },
  normalize_next_batch: {
    title: "数据规范化批次已完成",
    description: "当前批次已转换为统一的学生、教师或部门数据合同。",
    tone: "success",
  },
  validate_normalized_input: {
    title: "输入数据校验已完成",
    description: "服务端已检查规范化结果和异常标记。",
    tone: "success",
  },
  build_identity_index: {
    title: "身份索引已建立",
    description: "编号、手机号令牌和邮箱索引已经建立。",
    tone: "success",
  },
  construct_identity_work: {
    title: "对账工作项已构建",
    description: "需要分析的缺失、重复、多余和字段差异已经整理完成。",
    tone: "success",
  },
  analyze_next_batch: {
    title: "AI 分析批次已完成",
    description: "Agent 已为当前异常批次生成分析和治理方案。",
    tone: "success",
  },
  enter_aggregate_risk: {
    title: "异常分析已完成",
    description: "所有可执行异常已完成分析，即将汇总风险。",
    tone: "success",
  },
  aggregate_risk: {
    title: "风险与审批已汇总",
    description: "高风险操作已经按同类问题冻结为审批组。",
    tone: "warning",
  },
  compile_execution_plan: {
    title: "治理执行计划已生成",
    description: "已批准的方案已经编译为受控治理操作。",
    tone: "success",
  },
  execute_ready_operations: {
    title: "治理操作已执行",
    description: "当前可执行治理操作已完成，并进入结果验证。",
    tone: "success",
  },
  generate_terminal_report: {
    title: "正在生成任务报告",
    description: "治理事实与验证结果正在汇总为最终报告。",
    tone: "info",
  },
};

export function presentAgentEvent(event: AgentTaskEvent): PresentedAgentEvent {
  const time = formatAgentEventTime(event.created_at);
  const staticEvent = staticEvents[event.type];
  if (staticEvent) return { ...staticEvent, time };

  if (event.type === "graph.transitioned") {
    const action = graphActionEvents[payloadString(event, "action_id")];
    if (action) return { ...action, time };
    return {
      title: "任务步骤已完成",
      description: "任务已安全进入下一处理节点。",
      tone: "info",
      time,
    };
  }

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
    const categories = payloadStringArray(event, "failure_categories");
    return {
      title: "模型分析已暂停",
      description: blockedModelDescription(attempts, categories),
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

function payloadStringArray(event: AgentTaskEvent, key: string): string[] {
  const value = event.payload[key];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function blockedModelDescription(attempts: number, categories: string[]): string {
  const prefix = `本阶段共进行了 ${attempts} 次模型尝试。`;
  if (categories.includes("tool_argument_rejected")) {
    return `${prefix}模型生成的工具参数未通过本批证据清单校验；失败审计已保存，请终止任务后检查批次工具契约。`;
  }
  if (categories.includes("tool_authorization_failure")) {
    return `${prefix}后端授权状态与冻结任务上下文不一致，系统已停止盲目重试；任务数据和学校锁仍被安全保留。`;
  }
  if (categories.includes("tool_execution_failure")) {
    return `${prefix}受控数据工具执行失败；失败审计已保存，请终止任务后检查对应后端工具。`;
  }
  if (categories.some((category) => category.startsWith("evidence_manifest_"))) {
    return `${prefix}服务端冻结证据清单未通过校验；任务数据和学校锁仍被安全保留，请终止任务后查看失败审计。`;
  }
  if (categories.includes("model_input_contract_failure")) {
    return `${prefix}后端传给 Agent 的输入合同未通过校验；任务数据和学校锁仍被安全保留，请终止任务后查看失败审计。`;
  }
  if (
    categories.includes("model_contract_failure")
    || categories.includes("model_output_failure")
  ) {
    return `${prefix}模型输出未通过结构化结果校验；任务数据和学校锁仍被安全保留。`;
  }
  if (categories.length > 0 && !categories.includes("model_provider_failure")) {
    return `${prefix}Agent 受控处理未能完成；任务数据和学校锁仍被安全保留，请终止任务后查看失败审计。`;
  }
  return `${prefix}模型服务未能完成处理。任务数据和学校锁仍被安全保留，请终止任务后检查模型服务。`;
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

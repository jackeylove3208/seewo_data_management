import type { EntityType } from "../../types/domain";
import type { OperationType, RiskLevel } from "../../api/reconciliation";

export const operationLabels: Record<OperationType, string> = {
  create: "新增",
  update: "更新",
  move: "调整归属",
  disable: "停用",
  skip: "暂不处理",
  manual_review: "人工处理",
};

export const riskLabels: Record<RiskLevel, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
};

export const riskColors: Record<RiskLevel, "success" | "warning" | "error"> = {
  low: "success",
  medium: "warning",
  high: "error",
};

export const entityTypeLabels: Record<EntityType, string> = {
  organization_unit: "组织单位",
  class: "班级",
  teacher: "教师",
  student: "学生",
};

const labels: Record<string, string> = {
  name: "名称",
  code: "编码",
  employee_number: "教师工号",
  student_number: "学生学号",
  phone: "手机号",
  email: "邮箱",
  status: "状态",
  subject: "任教学科",
  grade: "年级",
  class_name: "班级名称",
  school_year: "学年",
  parent_source_id: "上级组织",
  department_source_id: "所属部门",
  class_source_id: "所属班级",
  member_source_id: "成员",
  container_source_id: "所属容器",
  role: "成员角色",
};

export function fieldLabel(field: string) {
  return labels[field] ?? "其他属性";
}

export function displayFieldValue(field: string, value: unknown) {
  if (value === null || value === undefined || value === "") return "未设置";
  if (field === "status" && value === "active") return "启用";
  if (field === "status" && value === "inactive") return "停用";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

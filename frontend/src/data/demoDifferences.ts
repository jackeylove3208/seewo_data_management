import type { DifferencePerson, EntitySummary, EntityType } from "../types/domain";

export const entityLabels: Record<EntityType, string> = {
  organization_unit: "部门",
  class: "班级",
  teacher: "教师",
  student: "学生",
};

export const demoDifferences: DifferencePerson[] = [
  {
    id: "teacher-zhang-san",
    entityType: "teacher",
    name: "张三",
    context: "高中部 / 高中语文组",
    issues: [
      {
        id: "issue-zhang-department",
        field: "所属部门",
        type: "structure",
        sourceValue: "高中语文组",
        targetValue: "高中教学部",
        recommendation: "建议将魔方所属部门调整为高中语文组",
        risk: "medium",
        selectable: true,
      },
      {
        id: "issue-zhang-phone",
        field: "手机号",
        type: "attribute",
        sourceValue: "138****1234",
        targetValue: "137****5678",
        recommendation: "联系方式冲突，建议人工核实后再更新",
        risk: "high",
        selectable: true,
      },
    ],
  },
  {
    id: "teacher-li-si",
    entityType: "teacher",
    name: "李四",
    context: "教务处",
    issues: [
      {
        id: "issue-li-missing",
        field: "教师记录",
        type: "missing",
        sourceValue: "李四 / 工号 T0082",
        targetValue: "无记录",
        recommendation: "建议在魔方新增教师李四并归入教务处",
        risk: "low",
        selectable: true,
      },
    ],
  },
  {
    id: "teacher-wang-wu",
    entityType: "teacher",
    name: "王五",
    context: "初中部 / 数学组",
    issues: [
      {
        id: "issue-wang-redundant",
        field: "教师状态",
        type: "redundant",
        sourceValue: "无记录",
        targetValue: "在职",
        recommendation: "三方系统已无此人，建议确认离职状态后停用",
        risk: "high",
        selectable: true,
      },
    ],
  },
  {
    id: "student-chen-xi",
    entityType: "student",
    name: "陈希",
    context: "高一（3）班",
    issues: [
      {
        id: "issue-chen-class",
        field: "所在班级",
        type: "structure",
        sourceValue: "高一（3）班",
        targetValue: "高一（2）班",
        recommendation: "建议将魔方班级归属调整为高一（3）班",
        risk: "medium",
        selectable: true,
      },
      {
        id: "issue-chen-phone",
        field: "联系电话",
        type: "attribute",
        sourceValue: "139****2088",
        targetValue: "未填写",
        recommendation: "建议补充联系电话",
        risk: "low",
        selectable: true,
      },
    ],
  },
  {
    id: "student-lin-yu",
    entityType: "student",
    name: "林宇",
    context: "初二（1）班",
    issues: [
      {
        id: "issue-lin-missing",
        field: "学生记录",
        type: "missing",
        sourceValue: "林宇 / 学号 S20240319",
        targetValue: "无记录",
        recommendation: "建议在魔方新增学生林宇",
        risk: "low",
        selectable: true,
      },
    ],
  },
  {
    id: "class-grade-one-three",
    entityType: "class",
    name: "高一（3）班",
    context: "高中部 / 2026 级",
    issues: [
      {
        id: "issue-class-name",
        field: "班级名称",
        type: "attribute",
        sourceValue: "高一（3）班",
        targetValue: "2026级3班",
        recommendation: "名称语义一致，建议保留魔方现有名称",
        risk: "low",
        selectable: true,
      },
    ],
  },
  {
    id: "department-teaching",
    entityType: "organization_unit",
    name: "教学管理中心",
    context: "校本部",
    issues: [
      {
        id: "issue-department-parent",
        field: "上级部门",
        type: "structure",
        sourceValue: "校本部",
        targetValue: "行政中心",
        recommendation: "建议调整上级部门，执行前确认下属班级影响",
        risk: "high",
        selectable: true,
      },
    ],
  },
];

export const demoEntitySummaries: EntitySummary[] = [
  { type: "organization_unit", label: "部门", sourceCount: 5, targetCount: 5, issueCount: 1 },
  { type: "class", label: "班级", sourceCount: 30, targetCount: 30, issueCount: 1 },
  { type: "teacher", label: "教师", sourceCount: 80, targetCount: 82, issueCount: 4 },
  { type: "student", label: "学生", sourceCount: 400, targetCount: 401, issueCount: 3 },
];

export function differencesFor(entityType: EntityType) {
  return demoDifferences.filter((person) => person.entityType === entityType);
}

---
name: aggregate-risk-approvals
version: 1.0.0
phase: aggregate_risk_and_approvals
allowed_tools: [read_approval, persist_approval]
input_schema: ApprovalAggregationInput
output_schema: ApprovalGroupDraft
---
只能对服务端提供的同类、高风险、版本冻结工作项生成审批组草案。不得改变成员、风险或操作，不得代替用户同意或拒绝。

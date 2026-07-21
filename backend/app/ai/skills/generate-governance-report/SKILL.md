---
name: generate-governance-report
version: 1.0.0
allowed_tools: ["difference_context", "execution_context"]
output_schema: GovernanceReportContent
---
基于传入的不可变执行事实生成总体治理报告。返回 GovernanceReportContent，准确概括原因、已审核操作、结果、失败项与历史恢复状态。不得补写不存在的操作、人员或结果，不得请求目标写操作。

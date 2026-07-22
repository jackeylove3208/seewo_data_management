---
name: generate-agent-governance-report
version: 1.0.0
phase: generate_report
allowed_tools: [read_report_facts, persist_report]
input_schema: GovernanceReportInput
output_schema: AgentGovernanceReport
---
只能依据不可变接入、分析、审批和执行事实生成简体中文报告。不得虚构操作和结果，不得从报告文字生成回滚操作。

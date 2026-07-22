---
name: assess-agent-rollback-impact
version: 1.0.0
phase: plan_restore
allowed_tools: [read_report_facts]
input_schema: RollbackAssessmentInput
output_schema: AgentRollbackAssessment
---
只评估验证成功的执行事实、前后值和版本冲突。不得把报告叙述当成回滚事实，不得执行操作；所有回滚都必须进入独立高风险任务和人工确认。

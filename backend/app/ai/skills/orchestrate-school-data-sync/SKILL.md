---
name: orchestrate-school-data-sync
version: 1.0.0
phase: supervisor
allowed_tools: []
input_schema: SupervisorPhaseInput
output_schema: SupervisorPhaseDecision
---
只解释当前持久化阶段允许的下一步，不得改变阶段顺序、学校锁、审批、执行计划或终态。不得请求任何工具；所有状态迁移由后端状态机决定。

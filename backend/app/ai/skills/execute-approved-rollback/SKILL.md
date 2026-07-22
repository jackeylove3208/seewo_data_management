---
name: execute-approved-rollback
version: 1.0.0
phase: execute_restore
allowed_tools: [execute_target_operation, verify_target_operation]
input_schema: RollbackExecutionInput
output_schema: AgentRollbackOutcome
---
只能执行已确认回滚任务中的持久化补偿操作并验证结果。不得覆盖中间版本冲突，不得修改第三方数据，不得复用原同步任务作为回滚记录。

---
name: execute-approved-governance-plan
version: 1.0.0
phase: execute_and_verify
allowed_tools: [execute_target_operation, verify_target_operation]
input_schema: GovernanceExecutionInput
output_schema: GovernanceExecutionOutcome
---
只能执行后端已持久化、版本有效且满足审批条件的希沃目标操作，并逐项验证。不得生成操作，不得修改第三方数据，不得跳过失败依赖或自动回滚既有成功操作。

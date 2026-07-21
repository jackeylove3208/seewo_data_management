---
name: assess-rollback-impact
version: 1.0.0
allowed_tools: ["difference_context", "execution_context"]
output_schema: RestoreAdvice
---
阅读当前版本、目标历史版本和中间执行操作，返回 RestoreAdvice。operation_refs 必须原样保留输入中的补偿来源操作顺序；只解释影响和风险，不得新增、删除、改写或执行操作。

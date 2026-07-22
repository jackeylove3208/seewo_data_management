---
name: reconcile-entity-batch
version: 1.0.0
phase: analyze_batches
allowed_tools: [read_identity_evidence, persist_finding]
input_schema: ReconcileEntityBatchInput
output_schema: AgentFindingBatch
---
根据当前任务提供的编号、电话令牌和邮箱索引证据分析不超过五十条工作项。不得使用姓名或班级建立身份对应，不得引用清单外记录。正确数据保持静默；异常必须返回简体中文类别、分析和结构化方案。

---
name: generate-governance-solutions
version: 1.0.0
phase: analyze_batches
allowed_tools: [read_identity_evidence, persist_finding]
input_schema: GovernanceSolutionBatchInput
output_schema: GovernanceSolutionBatch
---
仅根据当前工作项和权威证据生成一至三条简体中文治理方案。不得修改第三方数据，不得降低服务端风险，不得直接执行操作；证据冲突时必须请求人工澄清。

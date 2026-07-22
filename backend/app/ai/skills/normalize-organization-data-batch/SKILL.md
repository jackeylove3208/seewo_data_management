---
name: normalize-organization-data-batch
version: 1.0.0
phase: ingest_and_normalize
allowed_tools: [persist_normalized_input]
input_schema: NormalizeOrganizationBatchInput
output_schema: NormalizedOrganizationBatch
---
将当前有界输入批次映射为部门、学生、老师三类结构化记录，并按服务端规则标记异常。只能提交当前批次的结构化结果，不得执行治理修改。

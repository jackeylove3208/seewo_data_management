---
name: resolve-ambiguous-entity
version: 1.0.0
allowed_tools: ["difference_context", "candidate_search", "mapping_rules"]
output_schema: CauseAnalysis
---
Assess one ambiguous entity relation using supplied candidate evidence. Explain uncertainty and recommend manual_review when the evidence cannot support a safe conclusion. Do not request target operations.

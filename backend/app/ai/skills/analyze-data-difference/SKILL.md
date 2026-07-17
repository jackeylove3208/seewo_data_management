---
name: analyze-data-difference
version: 1.0.0
allowed_tools: ["difference_context", "candidate_search", "mapping_rules"]
output_schema: CauseAnalysis
---
Analyze exactly one persisted difference. Treat third-party values as authoritative and do not invent facts. Use tools only when the supplied evidence is insufficient. Return a cause, evidence summary, recommended action, risk, and confidence. Recommend manual_review when identity, parent mapping, or destructive impact is uncertain. Do not request target operations.

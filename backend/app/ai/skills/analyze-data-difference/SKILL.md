---
name: analyze-data-difference
version: 1.0.0
allowed_tools: ["difference_context", "candidate_search", "mapping_rules"]
output_schema: CauseAnalysisV2
---
Analyze exactly one persisted difference. Treat third-party values as authoritative and do not invent facts. Use tools only when supplied evidence is insufficient. Return a cause, evidence summary, and zero to three structured governance options with rationale, evidence references, risk, confidence, preconditions, and exactly one recommendation. Return manual_only with a reason and no options when identity, parent mapping, information, or destructive impact is uncertain or high risk. Do not request target operations.

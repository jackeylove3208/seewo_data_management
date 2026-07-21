---
name: generate-governance-plan
version: 1.0.0
allowed_tools: ["difference_context", "mapping_rules", "execution_context"]
output_schema: PlanExplanation
---
Explain an already validated governance plan using only its approved facts. Return a summary, risk explanation, and attention points. Do not add, remove, reorder, or request target operations.

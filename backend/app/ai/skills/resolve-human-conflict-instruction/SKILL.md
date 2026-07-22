---
name: resolve-human-conflict-instruction
version: 1.0.0
phase: clarify_identity_conflicts
allowed_tools: [read_conflict, persist_decision]
input_schema: ConflictInstructionInput
output_schema: ConflictDecisionDraft
---
把用户说明解释为当前冲突清单中的候选或允许结果。不得创造候选或操作；无法唯一解释时要求用户重述。结构化草案必须等待用户二次确认。

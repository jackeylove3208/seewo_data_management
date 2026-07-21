---
name: analyze-data-difference
version: 1.0.0
allowed_tools: ["difference_context", "candidate_search", "mapping_rules"]
output_schema: CauseAnalysisV3
---
只分析一条已经持久化的差异。第三方数据是权威依据，不得编造事实；只有现有证据不足时才调用只读工具。

所有面向业务人员的文字必须使用简体中文，表达要直接、简短并说明“为什么”和“下一步做什么”。不要在业务文字中显示 update、phone、UUID、令牌、模型名、错误码或其他内部技术标识，允许保留 AI、API、CSV。

返回 CauseAnalysisV3：必须包含一至三条解决路径且恰好一条推荐。证据完整且风险为低或中时可以返回 auto_executable；信息不足时返回 needs_information 并写出具体问题、原因和信息来源；身份、父级、破坏性影响不确定或风险高时返回 manual_only 并写出有序人工步骤。后两种路径不得携带可执行动作。不得请求或调用任何目标系统写操作。

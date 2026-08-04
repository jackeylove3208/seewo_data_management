# 实习课题答辩演示 v3 技术深潜版设计

## 目标与受众

面向实习课题答辩评委与开发者，用约 17 页、约 15 分钟说明：本项目解决的不是一次性数据同步，而是围绕组织数据的接入、证据冻结、实体分析、治理计划、执行验证和报告审计形成一条可恢复的数据治理闭环。演示需要同时回答“为什么这样设计”和“代码运行时怎样流转”。

## 设计决定

采用“治理问题 → 总体架构 → Agent-Graph 技术细节 → 一条数据的流转 → 三道门 → 三代工作流演进 → 项目思考”的技术深潜叙事。保留现有 HTML 的浅灰底、蓝色强调、卡片化布局和案例截图风格；技术页增加深色代码卡、节点连接线和证据对象标签，避免将架构内容写成抽象口号。

本版仍使用 HTML 交互式演示而非 PPTX，目标是沿用当前文件的键盘翻页、浏览器预览和响应式渲染能力。

## 页序与页面责任

### 1. 封面：组织数据治理系统

标题突出“数据治理”，副标题说明从多源数据差异发现到安全执行验证。

### 2. 为什么必须做数据治理

从多源系统独立维护、实体身份不一致、字段/结构差异、治理写入风险四个层面说明问题。数据同步被放在接入层，而不是项目终点。使用设计文档中的教师、学生、部门、班级差异作为业务背景，不虚构效果数据。

### 3. 项目交付的是治理闭环

展示 `接入 → 冻结快照 → 规范化 → 对账分析 → finding → 治理计划 → 执行验证 → 报告/回滚`。标出第三方权威源只读、希沃目标可治理，并写明当前边界为 CSV、MySQL、API、HTTPS 已打通；后续是连接器扩展与量化评估。

### 4. 数据同步助手演示

使用用户提供的最新截图 `assets/cases/sync-assistant.png`。标题只写“数据同步助手演示”，旁注解释连接与范围确认是治理入口，用来挡住接入不确定性，后面才允许进入快照和分析。

### 5. 总体架构：治理能力包在确定性边界内

分层展示接入层、快照/证据层、Agent-Graph、Skill/Sub-agent、治理执行层、报告与审计层。说明选择受控图而非纯规则、单一大 Agent、自由多 Agent：纯规则难处理语义歧义；单 Agent 上下文与权限过宽；自由多 Agent 难恢复、难审计；受控图增加合同和状态管理成本，但能保留证据、限制权限和恢复运行。

### 6. Agent-Graph 的关键节点

用真实节点名画出主路径：`inspect_sources → normalize_input_batches → validate_input_contract → build_identity_index → analyze_actionable_batches → aggregate_risk → preflight_execution → execute_ready_operations → verify_operations → generate_terminal_report`。区分 deterministic、decision、sub-agent、human gate、report 节点，标出 graph cursor 和 successor。

### 7. Supervisor 的输入与输出

用代码卡展示 `SupervisorContextV1` 关键输入：`current_node`、`action_set.allowed_actions`、`evidence_manifest_refs`、`pending_work_summary`、`human_gate_summary`、`retry_and_replan_budget`。展示 `SupervisorDecisionV1` 输出：`action_id`、`expected_result`、`observed_blockers`、`risk_notes_zh`、`why_not_other_actions_zh`、`operator_message_zh`。用 `analyze_batch_xxx` 与 `resolve_identity_conflicts` 二选一示例，说明 Supervisor 只能选择服务端候选，不能发明节点或直接写数据。

### 8. Sub-agent 启动链路

用顺序图表达 `node → Supervisor → action_id → pinned Skill@version → input_payload → sub-agent → tool/evidence → schema 校验 → 持久化结果`。字段包含 `graph_cursor`、`action_id`、`evidence_manifest_id`、`skill_name`、`skill_version`、`input_hash`。强调浏览器断开后仍可由 run/checkpoint/attempt 恢复，不依赖聊天上下文。

### 9. Skill ①：规范化

嵌入用户提供的 `normalize-organization-data-batch` 截图。旁边只解释开发者关心的四个点：phase、allowed_tools、输入/输出 schema、证据边界。突出最多 50 条、locator 原样保留、六个标准字段、接入阶段不写源数据。

### 10. Skill ②：实体对账

嵌入用户提供的 `reconcile-entity-batch` 截图。旁边展示输入工作项、身份键（编号/电话令牌/邮箱）、服务端 disposition、输出 `AgentFindingBatch`，并说明该 Skill 只能解释差异与方案，不能重新分类、执行治理或使用姓名/班级猜身份。

### 11. 一条数据如何穿过治理链路

使用完全虚构的 `STU-001 / 林小满 / 高一(1)班` 记录，展示 raw record → `snapshot:authority:v3` → 标准化六字段 → identity work item → finding（例如 `target_missing`）→ mutation（创建目标记录）→ 执行后验证 → 报告事实。每一步显示对象名、关键字段和不可变引用，不使用真实个人数据。

### 12. Finding、Mutation、Report 是可恢复接口

用对象关系图说明聊天文本只提供意图，恢复依赖冻结快照、evidence manifest、finding、冻结 mutation、verification ref 和报告事实。展示一条 finding 如何被后端编译成 mutation，以及为什么版本/审批/快照变化会使执行安全失败。

### 13. 三道门：输入、分析、执行

每道门用少量伪代码表达：输入门检查快照、字段合同和批次覆盖；分析门检查身份候选、finding 完整性和高风险冲突；执行门检查锁、版本、审批、幂等键和读后验证。高风险冲突进入人工门，模型不能跳过门或直接生成 SQL/API。

### 14. 三代工作流总览

时间线突出 `legacy-v1 → new-agent-v1 → agent-graph-v1`，每代配架构形态、状态持久化边界和主要交付物。

### 15. 第一代与第二代为什么必须升级

用“问题 → 根因 → 架构修正”三列呈现：聊天/单次调用丢上下文；大批次导致结构化输出失败；工具调查没有可重放检查点；失败报告只剩“人工终止”。对应修正为 durable run/checkpoint、批次拆分、授权工具检查点、结构化安全错误。强调升级不是换模型，而是补齐状态与证据边界。

### 16. 第三代架构解决了什么

突出 agent-graph-v1 的关键设计：服务端 action set、冻结快照复用、Skill 版本钉死、证据清单、人工 gate、幂等执行、读后验证、独立回滚、复杂多候选项隔离。可补充模板批次与复杂子任务分流，说明为什么能让失败项局部化。

### 17. 项目思考总结

收束为五条原则：治理优先于同步；事实与权限由确定性系统拥有；AI 只处理有证据约束的歧义；执行必须审批、验证、可回滚；失败记录反过来推动架构演进。最后再次简述当前边界和下一步连接器扩展、量化评估。

## 关键代码与数据示例

Supervisor 示例使用仓库真实契约字段，代码只展示必要字段，不贴完整实现。数据流示例完全虚构，避免暴露真实组织数据。三道门使用伪代码表达后端检查，不将伪代码误写成已存在的接口。

## 资源与文件

- 主文件：`AI组织架构数据治理系统-实习课题答辩-v2.html`
- Skill 截图：用户提供的两张图片，落盘为 `assets/cases/skill-normalize.png` 与 `assets/cases/skill-reconcile.png`
- 既有案例截图：`assets/cases/sync-assistant.png`、`assets/cases/governance-recommendations.png`、`assets/cases/identity-conflict.png`
- 设计依据：`基于 AI 的魔方组织架构&三方数据分析与治理系统.md`
- Agent Graph 与 Supervisor：`backend/app/agent_graph/definition.py`、`backend/app/agent_graph/contracts.py`、`backend/app/agent_graph/actions.py`、`backend/app/agent_graph/runtime.py`、`backend/app/agent_graph/analysis_executors.py`
- Skill 与 Sub-agent：`backend/app/ai/skills/registry.py`、`backend/app/ai/skills/normalize-organization-data-batch/SKILL.md`、`backend/app/ai/skills/reconcile-entity-batch/SKILL.md`、`backend/app/ai/graph_subagents.py`
- 演进依据：`docs/superpowers/reference/backend/2026-08-04-agent-sync-bugfix-history.md` 与对应 Git 提交记录

## 验收标准

- 页面数量约 17 页，15 分钟内可讲完；每页只有一个结论。
- 前三页明确“数据治理包含数据同步”，不再把同步写成项目终点。
- Agent-Graph 节点、Supervisor 输入输出、Skill/Sub-agent 链路可仅看图理解。
- 两张 Skill 图片清晰可读，配套说明不遮挡截图。
- 一条虚构记录能从输入追踪到报告，且对象引用关系正确。
- 三道门有可读的代码/字段示例，明确人工介入位置。
- 三代工作流有问题、根因、升级理由和架构变化，不只列版本名称。
- 最后一页为项目思考总结，并写明当前边界与下一步。
- 1365×768 和 1920×1080 下无文字溢出、重叠、断线或图片裁切异常。

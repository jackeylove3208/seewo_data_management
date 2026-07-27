---
name: assess-agent-rollback-impact
version: 2.0.0
phase: plan_restore
allowed_tools: [read_verified_mutations, read_restore_comparison_facts, submit_restore_assessment]
input_schema: RollbackAssessmentInput
output_schema: AgentRollbackAssessment
---
# Agent 回滚影响评估

## 身份与目标

担任回滚规划 sub-agent。针对一个已经产生验证成功目标 mutation 的原同步任务，根据服务端
确定性比较事实整理恢复影响，严格区分 `safe_to_restore`、`already_restored` 和 `conflict`。
本 Skill 只解释并提交服务端事实，不执行、不自行比较原始数据，也不能改变分类。每次回滚必须
创建新的独立任务、独立学校锁所有者、独立审批、独立报告和历史记录。

## 可信输入与证据边界

- 只使用 `original_task_id` 和 `verified_execution_refs` 指向的验证成功执行事实。失败、阻断、
  跳过、未验证尝试、AI 分析、治理方案和报告叙述均不能生成补偿操作。
- `before` 是原同步写入前的值，`after` 是原同步验证成功后的值，`current` 是本次回滚规划时
  从目标系统重新读取的实际值。三者的比较由服务端完成，并通过
  `read_restore_comparison_facts` 提供确定性分类、影响字段和 `comparison_hash`。
- 所有前后值证据都绑定到原验证成功操作；Skill 不能补齐、改写或自行推导缺失值。
- 目标版本 ID 不能作为数据是否被修改的判断依据。版本只用于审计、定位制品和并发追踪；
  即使版本 ID 已变化，也必须以本操作影响范围内的 actual `current` 数据比较结果为准。
- 不得从掩码值、报告文字或模型推断 `current`，不得把版本差异改写成数据冲突，也不得把
  服务端 `conflict` 降级为可恢复。
- 第三方权威数据始终只读，回滚只补偿希沃目标的已验证修改。
- 学生手机号仍是高风险隐私，只能使用令牌/掩码证据；所有回滚一律高风险并要求人工确认。

## 执行流程

1. 调用 `read_verified_mutations`，确认输入只包含当前租户原任务中验证成功的目标 mutation。
   没有验证成功事实时，输出三个空集合并说明不具备回滚依据。
2. 调用 `read_restore_comparison_facts`，取得每个操作唯一的服务端分类：
   `safe_to_restore`、`already_restored` 或 `conflict`。每条事实必须包含影响字段、
   原因码和 `comparison_hash`；缺项或操作集合不一致时停止，不能猜测。
3. 将 `safe_to_restore` 原样放入 `restorable_operation_ids`；将 `already_restored` 原样放入
   `already_restored_operation_ids`；将 `conflict` 原样放入 `conflict_operation_ids`。
   三个集合必须互斥并精确覆盖服务端绑定操作。
4. 按原执行事实和依赖顺序解释补偿：create 的补偿是删除原创建记录；update 只恢复原操作
   影响字段的 `before`；delete 仅在完整 `before` 和连接器支持创建时恢复记录。
5. 保持补偿依赖顺序。多个原操作部分成功时，只规划验证成功部分；不得为原失败/未执行项
   创建“回滚”。
6. 用简体中文说明将恢复什么、会影响哪些希沃记录、哪些操作已经处于回滚后状态、无法恢复
   的原因、当前冲突和人工确认要求。
7. 输出评估后等待用户确认。后端冻结 `comparison_hash`；本 Agent 不生成写参数，也不能用
   用户压力、期限或负责人指令覆盖服务端比较事实。

## 决策规则

- update：只比较原操作影响的字段。`current == after` 为 `safe_to_restore`；`current == before`
  为 `already_restored`；其他值或记录缺失为 `conflict`。回滚只写这些字段并保留无关字段。
- 原 create：当前记录不存在为 `already_restored`；当前记录仍与原创建后的完整记录一致为
  `safe_to_restore`；记录存在但内容已变化为 `conflict`，不得删除后续数据。
- 原 delete：当前记录仍不存在为 `safe_to_restore`；记录已存在且与完整 `before` 一致为
  `already_restored`；记录存在但内容不同为 `conflict`。
- 制品缺失、before/after 不完整、连接器不支持补偿或依赖不满足均为 `conflict`。版本 ID
  变化本身绝不是冲突理由。
- 不可作为依据：异常输入报告、模型失败报告、终止报告文字、AI 建议、未验证 success 声明。
- 原任务部分成功时，允许对成功子集建立独立回滚任务；不能要求整体回到一个虚构状态。
- 人工批准绑定操作集合和 `comparison_hash`。批准后、真正写入前若重新读取的相关 `current`
  产生不同哈希，旧批准失效并重新进入冲突与二次确认，不得静默继续。
- 回滚中再次发生冲突时进入受限人工澄清和二次确认，不得用“第三方仍是这样”强行覆盖希沃。
- 回滚任务被拒绝或终止时不执行补偿，仍生成独立报告并释放其学校锁。

## 输出要求

只输出 `AgentRollbackAssessment` 严格 JSON。`restorable_operation_ids`、
`already_restored_operation_ids` 和 `conflict_operation_ids` 只能逐项复制服务端分类，
三者互不重复且精确覆盖绑定操作；`impact_zh` 使用简体中文说明事实、范围、已恢复项、冲突
和风险；`requires_confirmation` 必须为 true。不得输出补偿 SQL、API 请求、绝对路径、
原始学生手机号、凭据、current 原始敏感值或报告叙述作为证据。

## 禁止事项

- 禁止执行回滚、修改第三方、复用原任务为回滚记录或绕过新的学校锁。
- 禁止从报告/分析文字推导操作，禁止补偿原本失败、阻断、跳过或未验证的操作。
- 禁止把版本 ID 变化当作数据变化；禁止整行恢复 update、覆盖无关字段、忽略 actual current
  冲突、伪造 before/after/current 或连接器能力。
- 禁止把 `already_restored` 放入待执行集合，禁止再次写入已经处于 before/缺失目标状态的项。
- 禁止把回滚评估标为低风险或代替用户确认。
- 禁止改变原执行事实、服务端分类、comparison_hash、依赖或审计记录。

## 停止条件

没有验证成功事实时，以不可回滚评估停止。比较事实缺失、集合不一致、制品缺失或能力不足时
失败关闭，不生成强制恢复。完成三态精确分区和中文影响说明后停止，等待独立回滚任务的人工
确认与服务端计划编译；版本变化但相关数据未变化时不得停在冲突。

# AI Supervisor 受控状态图设计

## 背景

当前 `new-agent-v1` 是一条由后端 Handler 固定推进的线性流程：

```text
ingest_and_normalize
  -> build_identity_work
  -> analyze_batches
  -> clarify_identity_conflicts
  -> aggregate_risk_and_approvals
  -> compile_execution_plan
  -> execute_and_verify
  -> generate_report
  -> terminal
```

对话意图识别和异常分析会调用模型，但数据接入、身份工作、冲突解释、审批聚合、治理执行、
报告和回滚主要由固定 Python 代码完成。多个阶段已经拥有完整 Skill，但运行时并未实际加载
这些 Skill 调用模型。因此当前架构是“AI 辅助的确定性流水线”，不是“AI Supervisor 调度
专职 sub-agent”。

本设计新增 `agent-graph-v1`，把新任务改造成“服务端定义状态图、AI Supervisor 从服务端允许
动作中选择、专职 sub-agent 在阶段内部规划、后端强制执行安全约束”的架构。

## 已确认的产品决策

- 使用服务端定义的受控状态图，不允许模型创建任意节点或边。
- Supervisor 可以自动进行低风险阶段内部回退，例如重新读取、重新映射和重新分析。
- 同一节点连续自动回退最多三次；超过上限进入人工或模型错误阻断。
- 跨阶段回退、任何目标写入前的重新规划、身份冲突、回滚和高风险操作需要人工确认。
- 第三方数据始终是只读权威数据；只有希沃目标允许被治理。
- 学生手机号仍是当前唯一高敏字段，模型边界使用任务级令牌。
- 服务端继续拥有租户、学校锁、状态图 guard、风险、审批、操作编译、版本、幂等、写入和审计。
- 正确数据保持静默；每条可操作异常必须有 AI 分析和治理方案。
- 模型单次调用失败仍采用初次调用加最多三次重试。
- 本次不开发登录、学校切换或角色系统；学校 ID 继续来自可信
  `OperatorContext.tenant_id`。

## 目标

- 让 Supervisor 真正决定当前允许动作中的下一步，而不是由 Handler 固定返回
  `next_phase`。
- 让接入、对账、冲突解释、执行监督、报告和回滚评估成为真正加载版本化 Skill 的 sub-agent。
- 允许阶段内部有界重试、重新取证和重新规划，同时避免无限循环。
- 为每次 Supervisor 决策、sub-agent 调用、MCP 工具调用和状态转换保留可恢复、可审计事实。
- 补齐权威—希沃完整配对证据，阻止 AI 基于单边记录生成分析。
- 保留 `legacy-v1` 和 `new-agent-v1` 历史任务的读取、报告、删除和回滚能力。
- 让前端显示业务可读的 Agent 行为与进度，而不是内部英文 phase 名称。

## 非目标

- 不允许模型生成或执行任意 SQL、Shell、URL、文件路径、凭据或连接器请求。
- 不允许模型直接决定学校锁、租户、风险等级、审批结果或任务终态。
- 不允许模型直接构造目标写入参数；写操作仍由服务端从持久化证据编译。
- 不把 embedding、向量数据库、Top-K 或旧 matching-quality gate 引入新图工作流。
- 第一阶段不开放未完成持久化接入的 API/数据库连接器；先完成 CSV/服务端授权本地 CSV。
- 不重写或删除已有 `legacy-v1`、`new-agent-v1` 数据。

## 方案选择

### 方案一：模型自由生成工作流

模型可以创建任意节点、边和工具调用。灵活性最高，但无法可靠保证审批、锁、隐私和写入安全，
不采用。

### 方案二：服务端定义图，AI 选择允许动作

服务端计算当前状态下的 `allowed_actions`，Supervisor 只能选择其中一个。sub-agent 只能调用
当前 action 授权的 MCP 工具。所有转换和写入仍经后端 guard。该方案兼顾 Agent 自主性、
可恢复性和安全性，采用此方案。

### 方案三：固定图，仅由 AI 生成说明

安全但本质仍是当前线性 Handler，不满足 Supervisor 驱动要求，不采用。

## 版本与兼容边界

### 工作流版本

- `legacy-v1`：继续使用历史匹配、差异和治理读取路径。
- `new-agent-v1`：保持当前固定线性流程，历史任务不可改变语义。
- `agent-graph-v1`：新建受控状态图任务。

功能开关新增：

```text
RECONCILIATION_AGENT_GRAPH_ENABLED=false
RECONCILIATION_AGENT_GRAPH_CSV_EXECUTION_ENABLED=false
```

只有 `NEW_AGENT_ENABLED=true` 且 `AGENT_GRAPH_ENABLED=true` 时，新入口才可创建
`agent-graph-v1`。开关变化不得修改已创建任务的工作流版本。

### 数据库迁移

只增加 append-only 表和字段，不修改已发布 Alembic revision。新 migration 必须基于合并时
最新 head，并通过干净 PostgreSQL migration 和历史任务读取测试。

## 三层权责模型

### AI Supervisor

- 从服务端给出的允许动作中选择一个。
- 决定调用哪个专职 sub-agent。
- 说明选择理由和预期证据。
- 在低风险范围内请求重新取证、重新映射或重新分析。
- 根据 sub-agent 结果和工具错误重新规划。
- 请求等待人工或进入事实报告。

### 专职 sub-agent

- 只处理一个有界 action 和一个版本化 evidence manifest。
- 加载一个或少量明确版本的 Skill。
- 通过阶段专属 MCP 工具读取证据和提交结构化草案。
- 不改变状态图，不释放学校锁，不直接写第三方。

### 确定性后端

- 生成图定义和当前允许动作。
- 校验 Supervisor 决策及 sub-agent 输出。
- 管理 tenant、task、run、lease、fencing 和学校锁。
- 构建 evidence manifest 并校验引用成员。
- 决定风险和人工 gate。
- 编译、执行、验证目标操作。
- 持久化转换、工具调用、事实、报告和回滚链。

## 受控状态图

### 节点类型

节点分为六类：

1. `decision`：Supervisor 从允许动作中选择下一动作。
2. `sub_agent`：运行一个版本化 Skill。
3. `deterministic`：执行后端索引、校验、编译、写入或验证。
4. `human_gate`：等待审批、冲突说明或二次确认。
5. `report`：生成事实和 AI 叙述。
6. `terminal`：完成、终止或失败。

### 同步主图

```text
intent_confirmed
  -> acquire_school_lock
  -> inspect_sources
  -> normalize_input_batches
  -> validate_input_contract
     -> abnormal_input_report        [整体结构不可识别]
     -> build_identity_index          [输入可继续]
  -> construct_identity_work
  -> analyze_actionable_batches
     -> repair_analysis_batch         [低风险自动回退，最多三次]
     -> resolve_identity_conflicts    [存在冲突]
     -> aggregate_risk                [无冲突或冲突已确认]
  -> wait_high_risk_approvals         [存在高风险]
  -> compile_execution_plan
  -> preflight_execution
     -> wait_replan_confirmation      [写入前发生跨阶段变化]
     -> execute_ready_operations
  -> verify_operations
     -> execute_remaining_independent [部分失败但仍有独立项]
     -> generate_terminal_report
  -> terminal
```

用户终止可以从任何非终态节点进入 `drain_current_atomic_unit -> termination_report`。模型重试
耗尽进入 `blocked_model_error`，保留学校锁，只允许显式终止。

### 回滚主图

```text
rollback_intent_confirmed
  -> acquire_school_lock
  -> load_verified_mutations
  -> assess_restore_impact
  -> wait_restore_conflicts
  -> wait_rollback_approval
  -> compile_restore_plan
  -> preflight_restore
  -> execute_restore_operations
  -> verify_restore_operations
  -> generate_rollback_report
  -> terminal
```

每次回滚都是独立 task、run、lock owner、报告和历史。

### 图 guard

每条边至少校验：

- 工作流版本、tenant、task、run 和 run kind。
- 当前节点版本和 graph cursor。
- lease owner、lease token 和 fencing token。
- 学校锁仍由当前 task/run 持有。
- 输入证据版本、目标版本和内容哈希。
- 所需 work unit 是否全部达到允许状态。
- 人工 gate 是否存在精确版本的确认。
- 当前 action 是否在 `allowed_actions`。
- 自动回退计数是否未超过三次。

模型不能覆盖 guard 结果。

## Supervisor 决策合同

### 输入

`SupervisorContextV1` 包含：

```text
tenant_ref
task_id
run_id
run_kind
workflow_version
graph_version
current_node
graph_cursor
status
allowed_actions
completed_action_summary
pending_work_summary
evidence_manifest_refs
human_gate_summary
connector_capability_summary
retry_and_replan_budget
termination_requested
```

`tenant_ref` 是不可反解的服务端引用，不向模型提供可覆盖的 tenant_id。

### 允许动作

每个 `allowed_action` 由服务端生成：

```json
{
  "action_id": "server-issued-id",
  "kind": "dispatch_sub_agent",
  "sub_agent": "reconciliation-analysis",
  "resource_ids": ["server-issued-work-unit-id"],
  "required_evidence": ["paired-record-evidence-v1"],
  "risk": "low",
  "requires_human": false
}
```

### 输出

`SupervisorDecisionV1`：

```json
{
  "action_id": "必须来自 allowed_actions",
  "reason_zh": "为什么选择该动作",
  "expected_result": "期望产生的证据类型",
  "operator_message_zh": "可选的业务进度说明"
}
```

Supervisor 不返回任意节点名称、不返回工具参数、不返回写操作。

## sub-agent 运行时

统一执行顺序：

1. 后端从 Supervisor decision 解析 action。
2. 加载固定 Skill 名称和版本。
3. 构建版本化 evidence manifest。
4. 签发 `AgentToolContext` 和最小 allowed capabilities。
5. 调用模型。
6. 若模型请求工具，先做服务端鉴权和参数校验。
7. 工具返回经过令牌化、掩码和资源裁剪的结果。
8. 模型继续，直到返回最终严格 JSON。
9. 服务端校验 schema、成员、引用、候选、字段、版本和操作白名单。
10. 原子持久化输出、provenance 和 action outcome。
11. 返回 decision 节点重新计算允许动作。

一次 invocation 最多八次工具往返，超过后视为有界执行失败。模型调用初次加三次重试；工具
业务拒绝不通过重复模型调用绕过。

## 专职 sub-agent

### 数据接入 Agent

加载：

- `inspect-external-data-source`
- `normalize-organization-data-batch`

职责：

- 识别 CSV 结构和稳定顺序。
- 分批读取最多五十行。
- 映射部门、学生、老师和六字段。
- 对第三方严格完整性和希沃身份键缺失进行标记。
- 无法识别整体结构时提交 abnormal-input verdict。

后端仍负责文件授权、页读取、稳定 locator、快照和持久化。

### 对账分析 Agent

加载：

- `reconcile-entity-batch`
- `generate-governance-solutions`

后端先构建普通 PostgreSQL 身份索引和完整 work item，模型不做向量检索。每个模型 work item
必须收到完整双边证据。

### 冲突解释 Agent

加载 `resolve-human-conflict-instruction`。只可从冻结候选和允许结果中解释自然语言，返回待
二次确认草案。模型无法唯一解释时要求重述。

### 执行监督 Agent

加载 `execute-approved-governance-plan`。只能从服务端列出的 ready operation ID 中选择下一
独立操作或建议暂停，不能生成 before/after 或连接器参数。后端执行并验证。

### 报告 Agent

加载 `generate-agent-governance-report`。统计数字和 rollback eligibility 由服务端生成；
模型只生成受事实约束的中文标题、总结、风险说明和后续建议。

### 回滚评估 Agent

加载：

- `assess-agent-rollback-impact`
- `execute-approved-rollback`

模型解释影响和冲突；后端只从 verified successful mutation 编译补偿操作并执行。

## MCP 工具集合

### 接入

- `inspect_configured_source`
- `read_connector_page`
- `submit_normalized_batch`
- `submit_input_marks`
- `submit_input_contract_verdict`

### 对账

- `read_work_item`
- `read_paired_record_evidence`
- `query_identity_postings`
- `read_claim_state`
- `submit_finding_batch`

### 人工与审批

- `read_frozen_conflict`
- `submit_conflict_interpretation`
- `read_frozen_approval_group`

人工同意/拒绝仍由 API 和后端持久化，模型没有 `approve` 工具。

### 执行

- `read_execution_plan`
- `read_ready_operations`
- `request_operation_execution`
- `read_operation_verification`

`request_operation_execution` 只接受已持久化 operation ID。

### 报告与回滚

- `read_report_fact_manifest`
- `submit_report_narrative`
- `read_verified_mutations`
- `read_restore_conflicts`
- `submit_restore_assessment`

不存在通用 SQL、任意文件、任意 URL、Shell、凭据或第三方写工具。

## evidence manifest

新增 `EvidenceManifestV1`，至少包含：

```text
manifest_id
tenant_ref
task_id
run_id
graph_node
action_id
snapshot_pair
target_version
resource_ids
allowed_evidence_refs
issued_sensitive_tokens
content_hash
created_at
```

对账使用 `PairedRecordEvidenceV1`：

```text
work_item_id
persisted_kind
entity_kind
target_record
authority_record
identity_key_hits
candidate_conflicts
authority_claim
target_stable_order
field_differences
allowed_candidates
allowed_operations
evidence_refs
```

字段差异必须比较所有适用治理字段：

- 部门、老师：category、name、number、phone、email。
- 学生：category、name、number、class_name、phone、email。

编号、电话、邮箱是身份候选键，但身份由其他键确认后，它们的缺失或不一致仍是普通治理差异。

模型返回的 evidence ref、candidate ID、work item ID、operation 和电话令牌必须属于 manifest
允许集合，否则整个 attempt 失败且不提交部分结果。

## 人工交互

正式 human gate：

- `identity_clarification`
- `clarification_confirmation`
- `high_risk_approval`
- `cross_phase_replan_confirmation`
- `rollback_approval`
- `restore_conflict_confirmation`
- `termination_confirmation`

普通高风险审批使用同意/拒绝卡，对话输入关闭。身份或回滚冲突临时开放输入；解释 Agent 返回
草案后关闭输入，显示确认/重述。每个决定冻结成员、版本、内容哈希、操作人和时间。

## 风险与执行安全

服务端风险政策不可被模型降低。至少以下为高风险：

- 学生手机号读取治理或变更。
- delete、disable、merge。
- create。
- 替换已有身份、类别或班级值。
- 任何回滚。
- 跨阶段重新规划后继续写入。

执行前后端重新计算：

- finding 和 solution 版本。
- 审批成员及内容哈希。
- expected-before。
- target version。
- connector capability。
- dependency readiness。
- idempotency key。

模型只选择已持久化 operation ID。第三方连接器永远没有 write capability。

## 自动回退和错误处理

### 低风险自动回退

允许：

- 同一来源页重新读取。
- 同一批重新规范化。
- 同一模型批因 JSON/schema/evidence 校验失败重新分析。
- 报告叙述重新生成。

约束：

- 同一 node/work unit 连续重新进入最多三次。
- 每次必须保留失败码、输入哈希、输出哈希和原因。
- 第四次仍失败进入 `blocked_model_error` 或 human gate。

### 必须人工确认的回退

- 已离开接入阶段后重新接入。
- 已完成分析后改变 evidence membership。
- 计划编译后重做身份或分析。
- 执行开始后改变计划。
- 任何可能改变已审批成员或目标版本的回退。

### 终止

终止请求立即阻止启动新 action。当前连接器原子单元安全完成或中止；已经 verified success 的
操作不自动回滚。Supervisor 不再规划普通工作，后端进入 termination report。

## 持久化模型

新增 append-only 记录：

- `AgentGraphRunRecord`：graph version、current node、cursor、decision budget。
- `AgentGraphTransitionRecord`：from、to、guard result、action、fencing。
- `AgentSupervisorDecisionRecord`：允许动作哈希、选择、理由、模型 provenance。
- `AgentEvidenceManifestRecord`：资源成员、版本和内容哈希。
- `AgentSubAgentInvocationRecord`：Skill、schema、attempt、lease、输入/输出哈希。
- `AgentToolCallRecord`：工具、参数哈希、结果哈希、授权结果、trace ID。
- `AgentHumanGateRecord`：gate 类型、冻结成员、版本、决定和确认。

所有完成 outcome 不可修改；重试新增 attempt。模型 prompt 和原始敏感 payload 不持久化。

## API 与前端

新增或扩展：

- 获取 graph run 摘要。
- 按 cursor 获取 Agent action/event。
- 获取当前业务阶段、sub-agent、动作和进度。
- 获取 human gate。
- 提交冲突说明和二次确认。
- 提交审批。
- 提交跨阶段重新规划确认。
- 终止任务。

前端继续显示四个业务大阶段：

1. 数据接入
2. Agent 分析与决策
3. 治理执行
4. 报告与回滚

大阶段内部显示当前 sub-agent 和可读动作，例如“正在检查第三方第 3 页”“正在分析学生异常
51/120”“等待确认 1 组身份冲突”。不显示内部 prompt、graph node ID、内容哈希或原始电话。

## 可靠性和可观测性

- 每个 action 使用 lease、heartbeat 和 fencing token。
- worker 重启后从未完成 action 恢复，不重复完成的模型调用和目标写入。
- Supervisor decision、sub-agent invocation 和工具调用使用稳定 idempotency key。
- 指标包含节点耗时、replan 次数、工具调用数、模型重试、human gate 等待时间、写入和回滚结果。
- sanitized error 只包含安全码、阶段、action、attempt、完成计数和安全 request ID。

## 测试策略

### 合同测试

- Supervisor 只能选择 allowed action。
- 图 guard 拒绝非法边、旧 cursor、过期 lease 和跨租户资源。
- Skill、input/output schema、MCP capability 精确绑定。
- evidence ref、candidate、token 和 operation membership 校验。

### 状态图测试

- 正常同步。
- 异常输入报告。
- 无异常直接报告。
- 身份冲突、重述和二次确认。
- 高风险同意和拒绝。
- 低风险自动回退三次。
- 第四次失败阻断。
- 跨阶段回退等待人工。
- 部分执行和独立继续。
- 每个节点终止。
- worker 崩溃恢复。
- 重复投递和 stale fencing。

### 安全测试

- prompt injection。
- 客户端 tenant 覆盖。
- 模型请求任意路径/SQL/URL/Shell。
- 第三方写入。
- 原始学生手机号泄漏。
- 伪造 evidence/candidate/token/operation。
- 绕过审批和版本检查。

### 端到端测试

- 对话入口 CSV 全生命周期。
- 手动入口 CSV 全生命周期。
- 报告与历史。
- 独立回滚。
- 前端刷新恢复。
- `legacy-v1` 和 `new-agent-v1` 历史兼容。

## 实施里程碑

### 里程碑一：图运行时与 Supervisor

- 添加 `agent-graph-v1` 和功能开关。
- 添加图定义、guard、决策/转换/manifest 持久化。
- 实现真实 Supervisor 调用。
- 初期 sub-agent action 可以委托现有确定性 Handler，行为与 `new-agent-v1` 等价。

### 里程碑二：证据与真实接入/分析 sub-agent

- 实现 MCP 工具循环。
- 接入 Agent 真正加载检查与规范化 Skill。
- 增加完整配对 evidence manifest。
- 修复 number/phone/email 普通字段差异。
- 强化模型输出引用成员校验。

### 里程碑三：冲突、治理、报告和回滚 sub-agent

- 实现自然语言冲突解释 Agent。
- 实现执行监督 Agent。
- 实现事实约束报告 Agent。
- 实现回滚影响 Agent。
- 保持风险、编译、写入和验证的服务端所有权。

### 里程碑四：前端、恢复和发布

- 接入 graph action/event 和 human gate。
- 显示业务可读动态进度。
- 完成崩溃恢复、并发、隐私和全链路测试。
- 先以分析/影子模式运行，再单独开启 CSV execution。

## 验收标准

- `agent-graph-v1` 的每个非确定性阶段都有真实 Supervisor 或 sub-agent 模型调用及固定 Skill。
- Supervisor 无法选择服务端未提供的 action。
- 所有模型可见记录都有版本化 evidence manifest。
- AI 分析拥有完整权威—希沃配对证据。
- 所有可操作异常均有中文 AI 分析和方案，正确记录不进入工作台。
- 低风险自动回退有上限，跨阶段和写入前回退需要人工确认。
- 模型不能直接修改第三方或构造目标写入。
- 刷新、重启、重复投递和模型失败不会重复已完成写入。
- 报告和回滚只使用不可变执行事实。
- 旧工作流任务仍可读取、报告、删除和回滚。

# 聊天驱动的第三方 API 连接器与同步设计

## 文档状态

本文描述在当前已经落地的 Agent Graph 架构上，接入钉钉、企业微信等第三方 API，
并把数据同步到模拟希沃的 MySQL 目标库。

本文描述的是基于当前 Agent Graph 的 API 接入设计合同，不代表当前代码已经支持
`api + database`。当前代码虽然在 Schema 层可表达 API connector，但任务运行时仍主要
支持 CSV/local、remote CSV/local 和 database/database。落地本设计必须新增
`source-ingestion-v3`，将接入阶段从单一 pair mode 改为按 `authoritative / target`
role binding 路由；同时让 API 任务选择 `agent-sync-graph-v2`，扩展
`materialize_sources` 对 `api-source` 的资源分派，并改造身份索引对外部身份绑定、
无身份键权威记录和 unavailable 字段的处理。旧任务必须继续按创建时冻结的 Graph 和接入
合同恢复，不能被 v3 逻辑隐式升级。

本版修正了早期设计中最重要的模型错位：

- 第三方 API 数据不进入旧的 `CanonicalEntityRecord / EntityMapping` 主流程。
- 所有来源最终都归一化为 `AgentInputRecord`。
- 身份对应由当前 Graph 的身份索引、`AgentWorkItemRecord` 和
  `AgentIdentityClaimRecord` 完成。
- AI 只分析身份索引产生的待分析小任务，不负责认证、HTTP 请求、分页或随意决定身份。
- API 来源复用 `agent-sync-graph-v2` 已有的 `materialize_sources` 节点。
- 新能力以 `source-ingestion-v3` 发布，不复制一份完整的 Graph v3。

## 结论

采用“通用连接器内核 + 供应商专用 Adapter + 现有 Graph v2”的方案。

对用户而言，流程是：

1. 在聊天中说“连接钉钉并同步到希沃 MySQL”。
2. Agent 识别供应商、目标连接和需要的实体类型。
3. 系统通过安全配置界面收集该供应商要求的参数；密钥不进入聊天。
4. 后端测试连接、权限和通讯录可见范围。
5. 用户确认同步范围后，只创建一个 `api + database` 任务。
6. Graph 获取学校锁，固化 API 来源，归一化两端数据，构建身份索引。
7. Graph 把差异拆成小任务，交给 AI 批量分析。
8. 治理层聚合风险、获取必要审批、编译执行计划。
9. 目标版本检查通过后写入 MySQL，并逐项验证和生成报告。

新增钉钉或企业微信时需要新增 Provider Manifest 和 Adapter，但不新增供应商专用 Graph
节点，也不发布一条供应商专用链路。

## 本版修正

| 主题 | 早期设计问题 | 本版设计 |
| --- | --- | --- |
| 规范数据 | 复用 `CanonicalEntityRecord` | Adapter 产生 `AgentContractRecord`，仓储持久化为 `AgentInputRecord` |
| 身份对应 | 复用旧 `EntityMapping` | 当前任务使用身份索引和 `AgentIdentityClaimRecord`；跨任务外部绑定使用新的 Agent 身份绑定合同 |
| Graph 版本 | 为 API 复制 `agent-sync-graph-v3` | 复用已有 `agent-sync-graph-v2` 拓扑，新增 `source-ingestion-v3` |
| API + Database | 用一个来源模式描述两端 | 按 `authoritative` 和 `target` 两个角色分别解析连接器类型、检查、映射和归一化 |
| 物化 | 物化后进入旧快照行和规范实体流程 | `SourceFile / Snapshot` 只保存证据与版本；业务记录直接进入 `AgentInputRecord` |
| Skill | 把来源检查和归一化都描述成 Skill 工作 | 已知 API Adapter 全程确定性执行；只有目标数据库字段映射确实不明确时才使用现有 Schema Skill |
| 外部 userid | 当成旧映射键或业务编号 | 只用于 API 稳定定位；不会默认写入 `number`，也不会成为普通身份 posting |
| 目标锁 | 把目标数据库描述成全程持锁 | 学校锁覆盖整个运行；目标库使用冻结版本、执行前版本检查、乐观并发和写后验证 |
| 验收 | 只检查连接和快照 | 覆盖 `AgentInputRecord → 身份索引 → work item → AI 批量分析 → 治理执行` 的完整事实链 |

## 目标

- 支持聊天驱动地选择、配置、测试并使用钉钉和企业微信连接器。
- 第一版支持第三方 API 作为权威来源、MySQL 作为目标。
- 目标 MySQL 用于模拟暂无真实 API 的希沃数据。
- 接入数据符合当前固定六字段 Agent 输入合同。
- 接入新供应商时不复制 Graph，不改后续分析和治理主链路。
- 连接器认证、限流、分页、重试、可见范围和错误翻译由确定性后端完成。
- 敏感凭据不进入 LLM、Skill、Graph checkpoint、日志或任务意图。
- 每个运行都可重放、可审计，并能说明数据来自哪个连接、哪个快照和哪个 Adapter 版本。

## 非目标

- 不支持 LLM 在运行时上网搜索任意 API 地址并生成未审核的 HTTP 调用。
- 不支持在聊天消息中直接提交 AppSecret、Secret 或访问令牌。
- 不让 LLM 持有凭据、刷新令牌或直接调用第三方认证接口。
- 第一版不提供任意 OpenAPI 文档导入和零代码连接器生成。
- 第一版不把第三方系统作为写入目标。
- 第一版不改变 AI 批量分析、风险审批、治理计划和 SQL 执行的总体职责。
- 不把钉钉 `userid`、企微 `userid` 或部门 ID 冒充为学校工号、学号或部门业务编号。
- 不复用旧流程的 `CanonicalEntityRecord`、`RawSnapshotRow` 或 `EntityMapping`。

## 当前架构基线

设计必须服从当前代码中的真实执行顺序：

```text
intent_confirmed
  → acquire_school_lock
  → materialize_sources                 # Graph v2
  → inspect_sources
  → normalize_input_batches
  → validate_input_contract
  → build_identity_index
  → construct_identity_work
  → analyze_actionable_batches
  → resolve_identity_conflicts（按事实需要）
  → aggregate_risk
  → wait_high_risk_approvals（按策略需要）
  → compile_execution_plan
  → preflight_execution
  → execute_ready_operations
  → verify_operations
  → generate_terminal_report
```

关键事实如下：

- `AgentContractRecord` 是进入 Agent 新流程前的不可变六字段投影。
- `AgentAnalysisRepository.persist_inputs()` 把它持久化为 `AgentInputRecord`。
- 身份 posting 当前只允许 `number`、`phone` 和 `email`。
- `AgentIdentityIndexBuilder` 基于 posting 产生匹配、冲突、目标多余、目标缺失和字段差异。
- 每个已接受的权威记录与目标记录对应关系，保存在本次运行的
  `AgentIdentityClaimRecord`。
- `construct_identity_work` 把 work item 规划为分析批次。
- `reconcile-entity-batch` 只读取已冻结的成对证据并提交分析结果。
- 风险聚合、审批冻结、计划编译、目标版本预检和执行验证继续使用现有实现。

因此，API 接入模块的交付边界是：

```text
第三方 API
  → 不可变 API 来源证据
  → AgentContractRecord
  → AgentInputRecord
```

一旦两端 `AgentInputRecord` 和输入标记已经正确持久化，后面的身份索引、work item、
AI 分析和治理链路应保持通用。

## 版本策略

当前系统同时存在多个彼此独立的版本轴，不能混为一谈。

### Workflow version

任务仍使用当前 Agent Graph 工作流标识。API 接入不另起一套工作流。

### Graph version

新 API 任务使用 `agent-sync-graph-v2`：

- v2 已经在学校锁之后提供 `materialize_sources`。
- API 来源同样需要在检查和归一化前固化远程权威数据。
- 后续节点、Guard 和人机门禁没有发生拓扑变化。

不发布 `agent-sync-graph-v3`。只有未来确实新增节点、删除节点、修改跳转或改变 Graph
Action/Guard 合同时，才发布新的 Graph 版本；届时也应从已有定义派生差异，不完整复制 v1。

### Ingestion contract version

新增 `source-ingestion-v3`，它表达本次真正发生的接入合同变化：

- 支持 `authoritative=api`、`target=database` 的混合角色。
- 来源检查、字段映射和归一化按角色路由。
- API 来源从物化证据确定性投影为 `AgentInputRecord`。
- API 权威字段缺失语义和外部身份绑定进入 Agent 身份索引。

已有任务继续使用其创建时冻结的 `model-mediated-ingestion-v1` 或
`source-ingestion-v2`，不在恢复运行时自动升级。

### Execution contract version

目标 MySQL 继续使用 `deterministic-execution-v2`。API 只改变权威来源，不改变 SQL
治理执行合同。

### Provider 和 Adapter version

每个快照还要冻结：

- `provider_id`
- `provider_manifest_version`
- `adapter_version`
- 字段投影版本
- 选择的实体类型
- 连接能力版本

这些版本用于重放和审计，不代替 Graph 或接入合同版本。

### 新 API 任务的版本组合

```json
{
  "workflow_version": "agent-graph-v1",
  "graph_version": "agent-sync-graph-v2",
  "ingestion_contract_version": "source-ingestion-v3",
  "execution_contract_version": "deterministic-execution-v2"
}
```

## 总体职责边界

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| 对话 Supervisor | 识别意图、选择已注册 Provider、补齐非敏感参数、请求确认 | 保存密钥、任意 HTTP、决定身份匹配 |
| 安全配置界面 | 收集供应商要求的凭据和组织参数 | 把明文回传聊天 |
| Provider Manifest | 声明官方端点、认证方式、权限、实体能力、限流和字段语义 | 运行时搜索网页 |
| Provider Adapter | 认证、分页、限流、重试、错误翻译和原始实体提取 | AI 推理、目标写入 |
| API Materializer | 将一次读取固化为任务绑定的不可变来源证据 | 归一化后续治理记录 |
| Agent API Ingestion Adapter | 将固化证据确定性投影为 `AgentContractRecord` | 再次请求第三方 API |
| 身份索引 | 使用身份 posting 和已确认外部绑定建立本次运行的 claim | 让 LLM 猜测 userid 对应谁 |
| AI Batch Analysis | 分析已构建的差异小任务 | 认证、分页、建索引、直接写目标 |
| Governance / SQL Handler | 风险、审批、计划、目标写入和验证 | 读取第三方凭据 |

## 连接器分层

### 通用内核

所有第三方 API Adapter 实现统一接口，概念合同如下：

```python
class OrganizationApiAdapter(Protocol):
    manifest: ProviderManifest

    async def test_connection(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
    ) -> ConnectionTestResult: ...
    async def capture(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        selected_entities: frozenset[AgentEntityKind],
    ) -> AsyncIterator[CapturedApiPage]: ...
```

接口通用，但实现必须按供应商独立开发。钉钉、企业微信的认证、分页参数、错误码和组织模型
不同，不能用一个“万能 HTTP + LLM”实现。Provider 只负责安全抓取并形成冻结记录；
`AgentApiIngestionAdapter` 再统一完成 `AgentInputRecord` 归一化，避免两套投影语义。

### Provider Manifest

每个 Provider 使用代码仓库内受审计的 Manifest，至少声明：

```yaml
provider_id: dingtalk
manifest_version: 1.0.0
adapter: DingtalkOrganizationAdapter
authority_only: true
supported_entities:
  - department
  - teacher
credential_fields:
  - app_key
  - app_secret
required_capabilities:
  - organization_read
endpoint_policy: audited_manifest_only
```

实际支持的实体和字段以对应供应商权限及 Adapter 合同为准。若供应商不能可靠区分教师和
学生，Adapter 不得依靠 LLM 猜测；任务创建时应拒绝不受支持的实体类型，或要求用户配置
明确的供应商字段规则。

### 连接实例

新增租户级 `ApiConnectionRecord`，建议字段：

- `id`
- `tenant_id`
- `provider_id`
- `display_name`
- `organization_ref`
- `public_configuration`
- `secret_ref`
- `manifest_version`
- `adapter_version`
- `capabilities`
- `visibility_summary`
- `state`
- `last_tested_at`
- `last_safe_error_code`
- `created_by`
- `updated_by`

`public_configuration` 只能保存非密钥参数。`secret_ref` 指向后端密钥存储，数据库不保存
AppSecret 明文。

访问令牌由 Adapter 在后端获取和刷新。若需要缓存，只能进入加密令牌缓存，并包含连接 ID、
租户 ID、过期时间和最小权限范围。

### 安全配置会话

聊天返回一次性的配置会话引用，前端打开安全表单。提交路径直接到后端配置接口：

```text
用户安全表单
  → Connector API
  → Secret Store
  → connection.secret_ref
```

以下位置不得出现明文密钥：

- 聊天消息和对话摘要
- LLM 请求和响应
- Skill 输入输出
- MCP 工具参数
- `ReconciliationTask.agent_intent`
- Graph checkpoint、事件和证据清单
- 应用日志、异常详情和前端埋点

如果用户已经把密钥发进聊天，系统应提示立即轮换，并且不得把该值复制进设计、测试夹具或
连接记录。

## 聊天与任务创建

### 对话职责

对话只收集以下非敏感决策：

- Provider，例如钉钉或企业微信。
- 已配置的连接实例。
- 目标数据库连接。
- 同步的实体类型。
- 全量或受支持的组织范围。
- 是否开始创建任务。

对话可以告诉用户缺少哪些配置，但不能询问用户把 AppSecret 直接发到聊天。

### 任务意图

API 与数据库使用现有 `AgentConnectorSelection.configuration_id` 表达连接选择：

```json
{
  "source": {
    "kind": "api",
    "configuration_id": "<api_connection_id>"
  },
  "target": {
    "kind": "database",
    "configuration_id": "seewo-mysql"
  },
  "entity_types": ["department", "teacher", "student"]
}
```

任务意图只保存连接引用，不复制连接配置和密钥。

### API + Database 运行时校验

`AgentTaskService._validate_connector_runtime()` 增加明确的混合角色分支：

```text
source.kind == api
and target.kind == database
```

任务创建前必须确认：

- API 连接属于当前租户且状态为 `active`。
- Provider 是只读权威来源。
- 连接最近一次测试通过，能力覆盖所选实体。
- API 通讯录可见范围不是空范围。
- 目标数据库连接属于当前租户，角色为 `target`。
- 目标连接支持读取、版本获取、参数化写入和写后验证。
- 第一版目标方言为 MySQL。
- 当前租户没有活动学校任务锁。

不能继续使用“CSV 对 CSV 或 SQL 对 SQL”的成对限制。

### 按角色路由

`source-ingestion-v3` 不再调用当前只返回单一模式的 `_source_pair_mode()` 或
`_task_source_mode()` 来决定两端行为，而是冻结两个 role binding：

| source role | connector kind | 检查 | 映射 | 归一化 |
| --- | --- | --- | --- | --- |
| `authoritative` | `api` | 检查物化证据和 Provider 能力 | Provider 固定投影 | `AgentApiIngestionAdapter` |
| `target` | `database` | 数据库健康、Schema 和版本 | 配置映射或现有数据库 Schema Skill | `AgentDatabaseIngestionAdapter` |

建议以 `AgentSourceBinding` 值对象统一传递：

```text
role
connector_kind
configuration_id
snapshot_id
mapping_checkpoint_key
normalization_checkpoint_key
```

所有 checkpoint 必须带 role，不能让权威 API 的映射覆盖目标数据库映射。

## API 来源物化

### 为什么先物化

第三方组织数据可能在分页期间变化，访问令牌会过期，接口返回顺序也可能不稳定。如果分析
阶段继续实时请求 API，同一次运行的证据会漂移，无法重放。

因此 Graph 必须先把本次权威读取固化，再进行检查和归一化。

### 复用 Graph v2

`agent-sync-graph-v2` 已有：

```text
acquire_school_lock
  → materialize_sources
  → inspect_sources
```

API 任务复用现有 Graph Action kind `materialize_remote_authority`。该名称作为 v2 的兼容
合同保留，执行器根据 task-bound resource 区分：

```text
remote-source:<id>   → Remote CSV materializer
api-source:<id>      → API authority materializer
```

API 候选 Action 使用：

```text
graph_action_kind: materialize_remote_authority
resource_id: api-source:<api_source_id>
required_evidence: api-source:<api_source_id>:materialized
```

这样无需修改 Graph 节点或跳转，也不会为每个连接发布新链路。

### 任务绑定记录

新增 `ApiAuthoritySourceRecord`，职责与远程 CSV 的任务绑定记录相似：

- `id`
- `tenant_id`
- `task_id`
- `connection_id`
- `provider_id`
- `selected_entities`
- `selection_hash`
- `state`
- `source_file_id`
- `snapshot_id`
- `content_sha256`
- `record_count`
- `page_count`
- `manifest_version`
- `adapter_version`
- `captured_at`
- `safe_problem_code`

创建任务时绑定连接和选择范围，但不提前读取 API。

### 固化过程

`ApiAuthorityMaterializer.capture()` 必须：

1. 校验 task、tenant、connection 和 `ApiAuthoritySourceRecord` 的绑定未变化。
2. 从后端 Secret Store 解析凭据。
3. 使用 Manifest 中的固定官方端点获取或刷新令牌。
4. 按 Adapter 规定的顺序读取部门和成员等资源。
5. 实施超时、限流、指数退避和供应商错误码翻译。
6. 把每条原始记录写入临时的规范 JSONL 制品。
7. 记录资源类型、外部 ID、页序号、页内序号和原始安全负载。
8. 校验分页闭合、重复游标、重复外部 ID 和所选实体覆盖情况。
9. 计算内容哈希并原子发布为托管 `SourceFile`。
10. 创建 authoritative `Snapshot`，把 `ApiAuthoritySourceRecord` 标为 `ready`。
11. 保存不含密钥和令牌的 materialization checkpoint。

归一化阶段只读取已经发布的制品，不再访问第三方 API。

### Snapshot 的边界

继续复用 `SourceFile` 和 `Snapshot`，因为当前 `AgentInputRecord.snapshot_id`、身份索引和
目标版本合同都依赖任务的成对快照。

但 Snapshot 在这里仅承担：

- 来源证据引用
- 内容哈希
- Adapter/Manifest/投影版本
- 记录数和完整性摘要
- 权威角色绑定

API 记录不会再转换为 `RawSnapshotRow` 或 `CanonicalEntityRecord`。

### 幂等与恢复

物化幂等键至少包含：

```text
task_id
api_source_id
connection_id
selection_hash
manifest_version
adapter_version
```

- 已 `ready` 且合同未变化时，重试返回同一个 `SourceFile / Snapshot`。
- 临时文件不会被 Graph 后续节点读取。
- 失败重试可以从 Adapter 声明为安全的游标恢复；否则重新抓取并原子替换临时制品。
- 内容变化不能覆盖已发布快照，只能产生新的运行或显式重采集。
- checkpoint 不保存 access token、请求头或第三方原始错误正文。

## 归一化为 AgentInputRecord

### 确定性 API Adapter

新增 `AgentApiIngestionAdapter`，形状与当前 `AgentDatabaseIngestionAdapter` 一致：

```text
冻结的 API JSONL
  → Provider Adapter 固定投影
  → AgentContractRecord
  → AgentAnalysisRepository.persist_inputs()
  → AgentInputRecord
  → AgentInputMarkRecord
```

每条 `AgentContractRecord` 仍使用当前固定字段：

- `entity_kind`
- `category`
- `name`
- `number`
- `class_name`
- `phone`
- `email`

并携带现有运行上下文：

- `task_id`
- `run_id`
- `snapshot_id`
- `tenant_id`
- `source_role`
- `stable_locator`
- `stable_order`
- `raw_row_number`

### 稳定定位

API 权威记录的稳定定位格式：

```text
api:<connection_id>:<entity_kind>:<encoded_external_id>
```

其中：

- `connection_id` 防止不同组织的相同 userid 冲突。
- `entity_kind` 防止部门 ID 与成员 ID 冲突。
- `external_id` 来自 Provider 明确声明的稳定技术 ID。
- Adapter 必须进行长度校验和可逆安全编码。

`stable_order` 使用固定实体优先级和外部 ID 的确定性排序，不依赖接口偶然返回顺序。
同一运行中相同 locator 但不同输入哈希仍触发当前 `ReplayConflict`。

### 字段语义

Provider Manifest 必须明确每个来源字段对应的六字段语义：

| Agent 字段 | 允许的来源 |
| --- | --- |
| `category` | Provider 明确的实体类别或已确认配置 |
| `name` | 部门名、人员显示名或学生姓名 |
| `number` | 学校业务工号、学号或部门业务编码 |
| `class_name` | 学生所属班级的明确字段 |
| `phone` | 有权限读取且已规范化的手机号 |
| `email` | 有权限读取且已规范化的邮箱 |

技术 userid、unionid、open_userid 和部门技术 ID 默认只进入 `stable_locator`，不写入
`number`。

只有管理员在 Provider 字段映射中明确确认某个扩展字段就是学校业务编号时，该字段才可
投影为 `number`。这个配置需要版本化和审计。

### 缺失字段与“未知”语义

当前 `source-ingestion-v2` 对 authoritative 记录要求多个字段同时存在。真实 API 可能因
权限或可见范围不返回手机号、邮箱，因此 v3 不能直接复用这一验证规则。

`source-ingestion-v3` 采用以下规则：

- `stable_locator`、`entity_kind` 和 `name` 是基本输入要求。
- 自动身份匹配使用 `number / phone / email`；没有这些字段时，身份索引先查询有效的外部
  身份绑定。
- 没有普通身份键且没有有效外部绑定时，身份索引持久化
  `authority_identity_absent` 标记，并创建 `authority_invalid` 工作项和必报异常，不让 AI
  猜测。
- API Adapter 不能在查询外部绑定前，仅因为缺少普通身份键就提前排除记录。
- 因权限不可见而缺失的普通字段记录为 `authority_field_unavailable`。
- 字段不可见与来源明确返回空值必须区分。
- 身份索引比较普通字段时跳过标记为 unavailable 的字段，避免把目标已有手机号或邮箱
  错误清空。
- 权威来源明确给出的空值是否允许清理目标数据，继续由治理策略和风险审批决定。

可以复用 `AgentInputMarkRecord.affected_fields` 保存本次记录不可治理的字段范围；
`ordinary_field_differences()` 在 v3 下必须读取这一字段范围。

## 身份索引与外部 userid

### 当前自动身份键不变

普通自动身份 posting 继续只使用：

```text
number
phone
email
```

API Adapter 不增加 `userid` posting，也不把姓名当成确定性身份键。

### 本次运行的身份事实

匹配成功后仍创建当前模型的 `AgentIdentityClaimRecord`：

```text
authority AgentInputRecord
  ↔ target AgentInputRecord
  ↔ AgentWorkItemRecord
```

后续成对证据、字段差异、AI 分析和治理计划只读取这个本次运行的 claim。

### 跨任务外部身份绑定

旧 `EntityMapping` 属于旧规范实体流程，不能直接复用。确实需要跨任务复用人工确认时，
新增 Agent 专用 `AgentExternalIdentityBindingRecord`：

- `id`
- `tenant_id`
- `provider_id`
- `connection_id`
- `entity_kind`
- `authority_stable_locator`
- `target_connector_id`
- `target_stable_locator`
- `status`
- `binding_version`
- `confirmed_by`
- `confirmed_at`
- `revoked_by`
- `revoked_at`
- `evidence_hash`

约束：

- 同一连接、实体类型和权威 locator 只能有一个活动目标。
- 同一绑定范围内的目标 locator 默认只能被一个权威 locator 占用。
- 绑定只建立关系，不把外部 userid 写入业务字段。
- 目标数据库主键或连接变化后，旧绑定不能自动迁移。
- 绑定失效、目标不存在或出现一对多时，必须进入现有身份冲突/异常事实。

### 身份索引顺序

`AgentIdentityIndexBuilder` 在 v3 下按以下顺序工作：

1. 加载两端 `AgentInputRecord` 和输入标记。
2. 验证并应用活动的 `AgentExternalIdentityBindingRecord`。
3. 为尚未 claim 的记录建立 `number / phone / email` posting。
4. 对既无 posting 又无有效绑定的权威记录持久化身份缺失标记并创建
   `authority_invalid`。
5. 对唯一候选创建 `AgentIdentityClaimRecord`。
6. 对多候选创建 `identity_conflict` 和现有澄清事实。
7. 对目标未匹配项创建 `target_extra`。
8. 对仍有可用身份、但没有目标 claim 的权威记录创建 `target_missing`。
9. 对其他被排除的权威项创建 `authority_invalid`。
10. 对已 claim 记录创建 `correct` 或 `field_difference`。

人工确认绑定必须通过明确的人机交互或管理接口产生，记录操作者和证据哈希。LLM 可以解释
候选差异，但不能自行创建持久绑定。

第一版若尚未实现外部绑定管理，则没有共同身份键的记录只能作为异常报告，不能为了提高
匹配率退回到按姓名或 userid 猜测。

## Work item、AI 分析与治理

API 接入完成后不新增新的供应商 work item 类型。继续使用当前类型：

- `resolved`
- `identity_conflict`
- `target_extra`
- `target_duplicate`
- `target_missing`
- `field_difference`
- `authority_invalid`
- `correct`

执行链路保持：

```text
AgentInputRecord
  → AgentIdentityPostingRecord / AgentExternalIdentityBindingRecord
  → AgentIdentityClaimRecord
  → AgentWorkItemRecord
  → AgentAnalysisBatchRecord
  → reconcile-entity-batch
  → finding
  → risk / approval
  → governance plan
  → SQL operation
  → verification
```

`construct_identity_work` 继续确定性创建批次。`reconcile-entity-batch` 只分析可执行的
work item 及其冻结证据；它不读取 API、不读取密钥、不新增身份候选。

## Skill 设计

### 不新增供应商 API Skill

第一版不新增：

- 钉钉 OAuth Skill
- 企业微信认证 Skill
- 任意 HTTP Skill
- API 分页 Skill
- API 字段猜测 Skill

这些工作由受审计的 Adapter 完成。

### 对话 Skill

更新现有对话 Skill，使其可以：

- 把“钉钉、企微、企业微信”解析为已注册 `provider_id`。
- 列出当前租户已配置且可用的连接引用。
- 说明需要打开安全配置界面。
- 收集实体范围、目标连接和用户确认。
- 生成不含凭据的任务意图。

对话 Skill 不获得 Secret Store、access token 或 API HTTP 工具。

### 接入阶段 Skill 范围

对于 `source-ingestion-v3`：

- API authoritative 检查：确定性，模型调用数为 0。
- API authoritative 归一化：确定性，模型调用数为 0。
- target database 检查：确定性，模型调用数为 0。
- target database 映射：若配置已完整，模型调用数为 0。
- target database 映射：只有 Schema 语义确实不明确时，才调用现有
  `understand-organization-database-schema`。

旧的 `inspect-external-data-source` 和 `normalize-organization-data-batch` 保留给旧接入
合同，不是 API v3 的主要执行路径。

`GRAPH_SKILL_TOOLS_BY_PHASE` 不需要增加认证或网络工具。即使接入阶段已有通用
`read_connector_page` 工具，API Secret-backed connector 也不得注册到 LLM Tool Gateway；
只有后端 Materializer 可以访问它。

### AI 批量分析

API 接入不修改 `reconcile-entity-batch` Skill 的职责。它继续在
`analyze_actionable_batches` 节点读取：

- work item
- claim 状态
- 成对记录证据
- 已冻结的字段差异

并输出受 Schema 校验的 finding。

## 目标数据库、锁和执行安全

### 学校锁

当前 `AgentSupervisorService.start()` 在进入接入阶段前获取租户学校锁。API 任务继续遵守：

- 锁在 `materialize_sources` 前已获取。
- 锁的 fencing token 随 Graph 运行传递。
- 同一租户不能同时创建第二个活动同步任务。
- 锁覆盖物化、分析、审批、计划和执行，直到终态或规定的终止处理完成。

连接创建和连接测试发生在同步任务外，不需要学校锁；它们不能直接创建第二个任务或写目标。

### API 来源

API 来源为只读，不获取第三方写锁。来源一致性由不可变物化制品保证。

若分页期间发现供应商版本或游标不一致，物化失败，不把半份数据发布为 Snapshot。

### 目标数据库

目标 MySQL 不宣称持有一个覆盖整个 Graph 的数据库锁。现有安全合同继续使用：

1. 目标归一化前后读取连接器版本，确保有界提取期间未变化。
2. 创建当前 `TargetVersion`。
3. 治理计划冻结该目标版本。
4. `preflight_execution` 比较计划版本、当前 `TargetVersion` 和数据库实时版本。
5. 版本漂移时进入已有 `cross_phase_replan` 人工门禁。
6. SQL Handler 使用参数化操作、幂等键和 `expected_version`。
7. 每个操作写后读取或调用 verify。
8. 每个成功操作产生新的目标版本。
9. 外部已执行但本地未记录的重试，使用现有幂等恢复逻辑。

API 接入不能绕过审批、目标版本预检或 SQL 验证。

## 错误处理

所有供应商错误必须翻译为稳定、安全的错误码。建议至少包括：

- `connector_credentials_invalid`
- `connector_permission_denied`
- `connector_visibility_empty`
- `connector_scope_incomplete`
- `connector_rate_limited`
- `connector_token_refresh_failed`
- `connector_remote_unavailable`
- `connector_schema_changed`
- `connector_pagination_incomplete`
- `connector_duplicate_external_id`
- `connector_materialization_conflict`
- `connector_entity_unsupported`
- `authority_identity_absent`
- `external_identity_binding_stale`
- `target_version_changed`

前端显示安全提示，不显示第三方完整响应、请求头、token 或密钥。

错误处理原则：

- 认证、权限和可见范围问题停在连接测试或物化阶段。
- 物化不完整不能进入 `inspect_sources`。
- 单条记录字段问题通过 `AgentInputMarkRecord` 进入异常事实。
- 身份冲突进入现有澄清链路。
- AI 分析失败按当前批次 claim/release 和 repair 机制处理。
- 目标版本变化进入 replan，不直接重试写入。
- 独立 SQL 操作的部分失败继续使用当前依赖和验证合同。

## 数据模型

### 新增

#### `ApiConnectionRecord`

保存租户级连接元数据、Provider/Adapter 版本、能力、状态和 `secret_ref`。

#### `ApiAuthoritySourceRecord`

保存一次任务对 API 权威来源的绑定、物化状态、SourceFile/Snapshot 引用和完整性摘要。

#### `AgentExternalIdentityBindingRecord`

保存经人工确认的 API 稳定 locator 与目标数据库稳定 locator 的跨任务对应关系。

如果第一版不交付人工绑定管理，可以先不创建该表，但必须保持“无业务身份键则异常”的安全
行为，不能退回复用旧 `EntityMapping`。

### 复用

- `SourceFile`：不可变 API JSONL 制品元数据。
- `Snapshot`：任务内权威或目标证据与版本边界。
- `AgentInputRecord`：统一的 Graph 输入记录。
- `AgentInputMarkRecord`：缺失、不可见、排除和异常字段事实。
- `AgentIdentityPostingRecord`：`number / phone / email` 身份索引。
- `AgentIdentityClaimRecord`：本次运行接受的权威与目标对应。
- `AgentWorkItemRecord`：身份与字段差异小任务。
- `AgentAnalysisBatchRecord`：AI 批量分析边界。
- `AgentGovernancePlanRecord` 和 operation 记录：治理执行。
- `TargetVersion`：目标数据库版本链。
- 现有学校锁、Graph run、checkpoint、事件和审计记录。

### 明确不复用

- `RawSnapshotRow`
- `CanonicalEntityRecord`
- 旧 `EntityMapping`

这些对象不属于当前 Agent Graph 主路径。API Graph 测试应明确断言不会创建它们。

## 后端接口

建议新增或扩展以下接口：

```text
GET    /api/connectors/providers
POST   /api/connectors/configuration-sessions
POST   /api/connectors/connections
GET    /api/connectors/connections
GET    /api/connectors/connections/{id}
POST   /api/connectors/connections/{id}/test
POST   /api/connectors/connections/{id}/rotate-secret
DELETE /api/connectors/connections/{id}

GET    /api/connectors/connections/{id}/capabilities
GET    /api/connectors/connections/{id}/visibility

GET    /api/agent/external-identity-bindings
POST   /api/agent/external-identity-bindings
DELETE /api/agent/external-identity-bindings/{id}
```

约束：

- 普通连接读取接口不返回 `secret_ref` 的内部值或任何明文。
- 创建和轮换密钥使用一次性配置会话与 CSRF 防护。
- 删除连接前检查是否被活动任务引用。
- 测试连接不创建 `ReconciliationTask`，也不读取超出最小探测范围的数据。
- 外部身份绑定的创建和撤销要求已认证操作者，并记录审计。

现有任务创建接口继续接收 `AgentConnectorSelection`。只需让运行时真正接受
`api + database`，无需增加另一套同步任务 API。

## 关键代码改动边界

### Graph 与运行时

- `backend/app/agent_runtime/service.py`
  - API 任务选择 `agent-sync-graph-v2`。
  - API 任务冻结 `source-ingestion-v3`。
- `backend/app/agent_graph/runtime.py`
  - `materialize_sources` 候选支持 task-bound `api-source`。
  - v3 检查和归一化候选按 role binding 路由。
  - 不再用单一 `_source_pair_mode()` 处理混合连接器。
- `backend/app/agent_graph/production_executor.py`
  - 现有 materialize Action kind 根据资源类型分派 Materializer。
  - 新增确定性的 API inspection 和 normalization 路径。
  - 目标数据库继续复用 v2 Adapter 和确定性执行。
- `backend/app/agent_graph/definition.py`
  - 第一版不新增 Graph 定义。

### 任务与连接

- `backend/app/agent_runtime/task_service.py`
  - 接受并验证 `api + database`。
  - 绑定 `ApiAuthoritySourceRecord` 和目标数据库 Snapshot。
- 新增连接器 Provider registry、connection service、secret resolver 和 API materializer。
- 不把供应商分支散落到 Supervisor；通过 registry 查找 Adapter。

### 接入与身份

- 新增 `backend/app/ingestion/agent_api_adapter.py`。
- 扩展 v3 输入验证和 unavailable 字段处理。
- `AgentIdentityIndexBuilder` 在 posting 前读取有效外部身份绑定。
- 普通 posting 仍只包含 `number / phone / email`。
- 不调用旧规范实体仓储。

### Skill

- 更新对话 Skill 的 Provider 与安全配置交互。
- 保持 API 认证和 HTTP 在 Skill 工具范围之外。
- 复用目标数据库 Schema Skill，不新增供应商 API Skill。
- 保持 `reconcile-entity-batch` 的输入职责不变。

## 测试策略

所有自动化测试只使用合成组织数据和假的 Adapter server，不使用真实师生记录或真实密钥。

### Provider Adapter 合同测试

每个 Adapter 运行相同合同套件：

- 正确读取多页部门和成员。
- 游标结束条件正确。
- 重复游标会失败。
- 重复外部 ID 会失败。
- 401/403、限流、超时和服务错误被翻译为安全错误码。
- token 刷新不泄漏凭据。
- entity capability 与 Manifest 一致。
- 同一原始实体生成相同稳定 locator 和六字段投影。
- userid 只进入 locator，不默认进入 `number`。
- 隐藏字段产生 unavailable 标记，不产生清空目标的差异。

### 物化测试

- API 任务在学校锁获取后才开始物化。
- task、tenant、connection 绑定不一致时拒绝物化。
- 完整分页产生一个托管 `SourceFile` 和一个 authoritative `Snapshot`。
- Snapshot 冻结 Manifest、Adapter、投影版本和选择范围。
- 已完成 Action 重试返回同一证据。
- 半成品和失败物化不会被后续节点读取。
- checkpoint 和日志不包含密钥、token、请求头或未脱敏错误正文。
- API 内容变化不会覆盖已发布 Snapshot。

### 接入合同测试

- authoritative API 记录转换为 `AgentContractRecord`。
- `persist_inputs()` 创建 `AgentInputRecord`。
- target MySQL 继续通过 `AgentDatabaseIngestionAdapter` 创建 `AgentInputRecord`。
- 两端 checkpoint 使用不同 role key。
- 已知 API 和已配置数据库映射的模型调用数为 0。
- 不创建 `RawSnapshotRow`、`CanonicalEntityRecord` 或旧 `EntityMapping`。
- v3 可重放时 locator、order 和 input hash 一致。
- 不同 input hash 重放同一 locator 时触发 `ReplayConflict`。

### 身份索引测试

- `number / phone / email` 唯一候选创建 `AgentIdentityClaimRecord`。
- 多候选创建 `identity_conflict`。
- 目标无候选创建 `target_extra`。
- 未 claim 权威记录创建 `target_missing`。
- 排除记录创建 `authority_invalid`。
- 外部 userid 不进入 `AgentIdentityPostingRecord`。
- 有效外部绑定在 posting 前创建本次运行 claim。
- 失效绑定不会静默匹配。
- 无业务身份键且无外部绑定时进入异常，不按姓名猜测。
- unavailable 字段不进入 `ordinary_field_differences()`。

### AI 与治理链路测试

- work item 被规划为 `AgentAnalysisBatchRecord`。
- `reconcile-entity-batch` 只收到冻结的 work item 和成对证据。
- AI 不收到连接配置、密钥、token 或原始 API 请求。
- 分析批次失败会释放 claim 并按现有 repair 机制重试。
- finding 进入风险聚合和审批分组。
- 计划编译使用当前目标 Snapshot 和 TargetVersion。

### SQL 执行测试

- `api + database` 任务选择 SQL Governance Handler。
- 计划前目标版本、实时数据库版本和计划版本一致时才执行。
- 版本漂移创建 `cross_phase_replan` 人工门禁。
- 写入使用幂等键和 expected version。
- create/update/delete 均执行写后验证。
- 部分操作失败不破坏独立操作的既有执行语义。
- 重试能识别外部已执行操作并恢复本地事实。

### Graph 测试

- API 新任务使用 `agent-sync-graph-v2`。
- API 新任务使用 `source-ingestion-v3`。
- API 新任务使用 `deterministic-execution-v2`。
- 路径包含 `acquire_school_lock → materialize_sources → inspect_sources`。
- 物化成功后才进入归一化。
- 后续路径实际经过身份索引、work item、AI 批次、风险、计划、预检、执行和报告。
- 钉钉与企业微信使用相同节点集合。
- 新增连接实例不会创建 Graph 定义。
- 恢复旧运行时继续使用其原有版本合同。

### 安全测试

- 租户不能读取或使用其他租户连接。
- Secret Store 解析只在后端 Adapter 边界发生。
- 配置 API、错误响应、审计和日志均不回显秘密。
- 测试连接只做最小读取。
- 通讯录可见范围为空或明显不足时不能创建同步任务。
- 外部身份绑定创建、撤销和冲突都有操作者审计。

## 验收标准

功能完成必须同时满足以下条件：

1. 用户能在聊天中选择钉钉或企业微信，但凭据通过安全表单提交。
2. Provider 端点来自受审计 Manifest，运行时不让 LLM 搜索或拼装任意 URL。
3. 连接测试能区分认证失败、权限不足、可见范围为空、限流和远端异常。
4. 明文凭据和 access token 不进入聊天、LLM、Skill、MCP、任务意图、checkpoint 或日志。
5. 用户确认后只创建一个 `source=api、target=database` 的同步任务。
6. 任务创建会分别验证权威 API 角色和目标数据库角色，不要求两端连接器类型相同。
7. 新任务冻结为 `agent-sync-graph-v2 + source-ingestion-v3 + deterministic-execution-v2`。
8. Graph 在学校锁之后、来源检查之前通过 `materialize_sources` 固化完整 API 权威数据。
9. 物化结果有任务、租户、连接、选择范围、内容哈希、Manifest 和 Adapter 版本证据。
10. API 记录确定性转换为 `AgentContractRecord` 并持久化为 `AgentInputRecord`。
11. 目标 MySQL 记录也持久化为同一类型的 `AgentInputRecord`。
12. API 主路径不会创建 `RawSnapshotRow`、`CanonicalEntityRecord` 或旧 `EntityMapping`。
13. 外部 userid 只作为稳定 locator 或显式外部绑定键，不默认成为 `number` 或 identity posting。
14. 身份索引实际生成 posting、claim 和 `AgentWorkItemRecord`。
15. 无可靠身份键的记录进入 `authority_invalid`/必报异常，不由 LLM 猜测。
16. 已知 API 来源的检查和归一化模型调用数为 0。
17. AI 只在 `analyze_actionable_batches` 处理已冻结 work item。
18. finding 实际进入风险聚合、审批、治理计划和 SQL operation。
19. 目标版本漂移会暂停并请求 replan 确认，不直接写入。
20. SQL 写入具备幂等键、expected version 和写后验证。
21. 钉钉和企业微信共用同一 Graph 节点集合；新增连接不会发布新链路。
22. 旧运行可按原 Graph 和接入合同恢复，不被 v3 接入逻辑改变。
23. 合同测试、集成测试、Graph 测试、安全测试和现有质量门禁全部通过。

## 开发顺序

### 第一阶段：版本与角色路由

- 增加 `source-ingestion-v3` feature flag 和运行冻结规则。
- API 任务选择现有 Graph v2。
- 把接入候选和执行器从单一 pair mode 改为按 role binding 路由。
- 保持旧 v1/v2 行为和恢复兼容。

### 第二阶段：连接器控制面

- Provider registry 和 Manifest。
- `ApiConnectionRecord`、Secret Store 引用和安全配置会话。
- 连接测试、能力检查、可见范围摘要和错误翻译。
- 对话 Skill 只接触安全连接视图。

### 第三阶段：钉钉纵向切片

- 钉钉 Adapter。
- `ApiAuthoritySourceRecord`。
- API Materializer 和 Graph v2 Action 分派。
- 不可变 JSONL、SourceFile、Snapshot 和完整性 checkpoint。

### 第四阶段：Agent 输入链

- `AgentApiIngestionAdapter`。
- v3 字段缺失和 unavailable 语义。
- `AgentInputRecord`、marks 和 role-specific checkpoints。
- API + MySQL 两端输入合同集成测试。

### 第五阶段：身份与治理链

- 证明 posting、claim、work item、AI 批次和治理计划完整贯通。
- 若本期需要，增加 `AgentExternalIdentityBindingRecord` 和管理接口。
- 目标版本漂移、SQL 执行、验证和重试测试。

### 第六阶段：企业微信

- 企业微信 Manifest 和 Adapter。
- 复用同一连接控制面、物化器、API Ingestion Adapter 和 Graph v2。
- 运行完整 Adapter 合同套件，不新增 Graph 节点。

### 第七阶段：安全与发布

- 凭据泄漏扫描。
- 多租户与可见范围测试。
- synthetic sandbox 烟雾测试。
- 现有后端、前端、迁移和 OpenSpec 质量门禁。

## 最终评估

这项功能对整体 Agent Graph 的影响是有限但明确的：

- Graph 拓扑不变，复用 v2 的远程来源物化节点。
- 接入合同升级为 v3，以支持每个角色使用不同连接器。
- 数据接入的终点改为当前真实的 `AgentInputRecord`。
- 身份索引、待分析小任务、AI 批量分析和治理执行继续作为唯一后续主链路。
- 外部 userid 被限制在稳定定位和显式绑定层，不污染学校业务字段。
- 新供应商通过 Adapter 扩展，不通过复制 Graph 或赋予 LLM 任意网络能力扩展。

因此，“新增数据来源只改接入层，后续不用改”的准确说法是：

> 新增供应商时只新增 Manifest 和 Adapter；首次增加 API 这一连接器大类时，需要升级接入
> 合同、任务角色路由、物化器和身份绑定边界。完成这些通用能力后，身份索引之后的分析与
> 治理链路不再因钉钉、企微或新增连接实例而变化。
